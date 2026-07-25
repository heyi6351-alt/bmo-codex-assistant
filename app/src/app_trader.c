/**
 * @file app_trader.c
 * @brief Trader facet - a pocket Bloomberg terminal on BMO's screen.
 *
 * The same Orange Pi 3B that hosts BMO's brain also runs a little Flask API
 * that scores markets (PandaAI). Trader polls it and renders a dense, dark
 * trading terminal: live price hero, direction pill, a candlestick chart with
 * support/resistance levels, an ENTRY/SL/TP trade ladder, and stat chips.
 *
 *   GET /signal?symbol=XAUUSD&profile=aggressive&tf=15min
 *       -> {"direction":"long|short|flat", "last_price":N, "entry":N,
 *           "stop_loss":N, "take_profits":[N,...], "risk_reward":N,
 *           "confidence":0..1, "regime_ok":B, "invalidation_reason":"...",
 *           "generated_at":"...", "indicators":{rsi14,macd,macd_hist,adx14,
 *           atr14,bb_upper,bb_lower,ema_fast,ema_slow,ema200,support,
 *           resistance,stoch_k,stoch_d}, "position_size":{note,risk_pct}}
 *   GET /candles?symbol=XAUUSD&tf=15min&n=48
 *       -> {"symbol":"XAUUSD","tf":"15min","c":[{"t","o","h","l","c"},...]}
 *
 * The JSON shape may drift (the API is being extended), so every field is
 * parsed defensively: a missing field keeps the last known good value, a
 * missing /candles endpoint just hides the chart and keeps the stats.
 *
 * Threading: identical to app_brain.c - all network I/O happens on the
 * "trader" worker thread, which fills a staging snapshot and raises a
 * volatile flag; an lv_timer notices the flag, copies the snapshot and
 * repaints (including the candle canvas). LVGL objects are only ever touched
 * from the lv_timer, i.e. the LVGL thread.
 *
 * Touch: tapping the chart cycles the symbol XAUUSD -> EURUSD -> GBPUSD ->
 * US30. The tap only bumps a volatile index; the worker picks it up, resets
 * the snapshot and re-fetches. No voice dependency.
 */
#include "kaleido.h"

#include "tal_api.h"
#include "http_client_interface.h"
#include "cJSON.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <ctype.h>

/***********************************************************
*********************** configuration **********************
***********************************************************/
/* The Orange Pi 3B's LAN address and the PandaAI Flask port. Plain HTTP on
 * purpose: no TLS, no cert, no cloud round-trip. */
#define TRADER_PI_HOST      "10.68.11.43"
#define TRADER_PI_PORT      8100
#define TRADER_HTTP_TIMEOUT 2500 /* keep the offline path snappy on stage */
#define TRADER_MAX_BODY     6144 /* biggest JSON body we will copy out    */
#define TRADER_POLL_MS      200  /* UI poll timer                         */
#define TRADER_REFRESH_MS   5000 /* worker re-polls /signal every ~5s     */
#define TRADER_CANDLE_EVERY 6    /* ...and /candles every 6th poll (~30s) */
#define TRADER_STEP_MS      500  /* worker sleep step (symbol watch)      */

#define TRADER_TF_QUERY     "15min"
#define TRADER_TF_LABEL     "15m"
#define TRADER_MAX_CANDLES  64
#define TRADER_N_CANDLES    48

/* Chart canvas geometry (RGB565, buffer lives in PSRAM). */
#define CH_W   440
#define CH_H   132
#define CH_AX  64 /* right axis zone width (price labels) */

#define CHG_Y  34 /* +/-% chip base y inside the hero row (tick pop anim) */

/* Symbols the chart tap cycles through. */
static const char *const S_SYMBOLS[] = {"XAUUSD", "EURUSD", "GBPUSD", "US30"};
#define TRADER_N_SYMBOLS ((int)(sizeof(S_SYMBOLS) / sizeof(S_SYMBOLS[0])))

/***********************************************************
************************* terminal theme *******************
***********************************************************/
/* "Pocket Bloomberg": BMO's serious mode - deliberately darker than the mint
 * face. All colors are plain hex; lv_color_hex() is applied at use. */
/* The two big flat fills are RGB565-exact (R/B low 3 bits, G low 2 bits zero)
 * so the value stored is exactly the value the panel shows - no rounding
 * delta between the canvas pixels and LVGL-rendered panels. */
#define COL_BG     0x081410 /* near-black teal (565-exact) */
#define COL_PANEL  0x182420 /* panel fill (565-exact)      */
#define COL_LINE   0x1E3A34 /* hairlines / chart grid     */
#define COL_TEXT   0xE8F5F0 /* primary text               */
#define COL_DIM    0x7FA69C /* dim text                   */
#define COL_BULL   0x2ECC71 /* long / up                  */
#define COL_BEAR   0xE74C3C /* short / down               */
#define COL_AMBER  0xF6C453 /* accent (entry, highlights) */
#define COL_FLAT   0x3A4A46 /* flat pill                  */
#define COL_SUP    0x1B5E3C /* support line (dim green)   */
#define COL_RES    0x7A2A22 /* resistance line (dim red)  */

/* 0xRRGGBB -> packed RGB565 (LV_COLOR_16_SWAP is 0 on this board). */
#define PX565(c) ((((c) >> 8) & 0xF800) | (((c) >> 5) & 0x07E0) | (((c) >> 3) & 0x001F))

/***********************************************************
************************ data model ************************
***********************************************************/
typedef struct {
    double o, h, l, c;
} CANDLE_T;

typedef struct {
    int    online;         /* 1 = last /signal poll actually reached the API */
    char   symbol[16];
    char   direction[8];   /* "long" / "short" / "flat" (lower-case) */
    double last_price;
    int    has_price;
    double entry, stop_loss, tp1, risk_reward, confidence;
    int    has_entry, has_sl, has_tp, has_rr, has_conf;
    int    regime_ok;
    char   invalidation[128];
    char   generated_at[24];
    double rsi14, macd_hist, adx14, atr14, support, resistance;
    int    has_rsi, has_macdh, has_adx, has_atr, has_sup, has_res;
    int    candles_ok;     /* 1 = /candles endpoint answered last time */
    int    n_candles;
    CANDLE_T candles[TRADER_MAX_CANDLES];
} TRADER_SNAP_T;

/* s_snap is touched only by the LVGL thread; s_work/s_stage only by the
 * worker (s_work is its persistent last-good state, s_stage the handoff).
 * s_ready is the one-way flag between them (bumped last by the worker). */
static TRADER_SNAP_T s_snap;
static TRADER_SNAP_T s_stage;
static TRADER_SNAP_T s_work;
static volatile int  s_ready = 0;

/* Symbol cycling: the chart tap (LVGL thread) bumps this, the worker picks
 * it up, resets its state and re-fetches. */
static volatile int s_sym_req = 0;

static THREAD_HANDLE s_worker = NULL;

/* ---- LVGL objects (built once, LVGL thread only) ---- */
static lv_obj_t *s_sym_lbl;
static lv_obj_t *s_live_dot, *s_live_lbl;
static lv_obj_t *s_price_lbl, *s_chg_lbl;
static lv_obj_t *s_dir_pill, *s_dir_lbl;
static lv_obj_t *s_canvas, *s_chart_msg, *s_max_lbl, *s_min_lbl;
static lv_obj_t *s_entry_lbl, *s_sl_lbl, *s_tp_lbl, *s_notrade_lbl;
static lv_obj_t *s_rsi_val, *s_macd_val, *s_adx_val, *s_atr_val;
static lv_obj_t *s_foot_lbl;
static uint16_t *s_cbuf = NULL; /* CH_W*CH_H RGB565 canvas pixels (PSRAM) */
static lv_obj_t *s_hero;   /* hero row container (fades on symbol switch) */
static lv_obj_t *s_panel;  /* chart panel (fades on symbol switch)        */
static lv_obj_t *s_ladder; /* trade ladder strip (NO TRADE pulse)         */

/* Animation state (LVGL thread only). */
static volatile int s_reveal = -1; /* candles shown by the draw-in sweep; -1 = all */
static char   s_revealed_sym[16];  /* symbol the chart already swept in for */
static int    s_blink = 0;         /* scanline phase (toggled by the blink timer) */
static int    s_fading = 0;        /* 1 = hero+chart faded out, swap pending */
static int    s_ladder_pulsing = 0;
static double s_prev_price = 0.0;
static int    s_prev_price_ok = 0;

/***********************************************************
************************* helpers **************************
***********************************************************/
static void __cp(char *dst, size_t n, const char *src)
{
    if (n == 0) {
        return;
    }
    if (src == NULL) {
        dst[0] = 0;
        return;
    }
    strncpy(dst, src, n - 1);
    dst[n - 1] = 0;
}

static const char *__jstr(cJSON *o, const char *k)
{
    cJSON *i = cJSON_GetObjectItem(o, k);
    return (i && cJSON_IsString(i) && i->valuestring) ? i->valuestring : NULL;
}

/* Number fields may come back as a JSON number or a numeric string. */
static int __jnum(cJSON *o, const char *k, double *out)
{
    cJSON *i = cJSON_GetObjectItem(o, k);
    if (i && cJSON_IsNumber(i)) {
        *out = i->valuedouble;
        return 1;
    }
    if (i && cJSON_IsString(i) && i->valuestring) {
        char  *end = NULL;
        double v   = strtod(i->valuestring, &end);
        if (end != i->valuestring) {
            *out = v;
            return 1;
        }
    }
    return 0;
}

/* Price decimals depend on the symbol: FX pairs want 4, US30 wants 1. */
static void __fmt_price(char *dst, size_t n, const char *sym, double v)
{
    if (strstr(sym, "EUR") || strstr(sym, "GBP")) {
        snprintf(dst, n, "%.4f", v);
    } else if (strstr(sym, "US30")) {
        snprintf(dst, n, "%.1f", v);
    } else {
        snprintf(dst, n, "%.2f", v);
    }
}

/* "generated_at" arrives as an ISO-ish timestamp; show just the HH:MM:SS. */
static void __fmt_time(char *dst, size_t n, const char *iso)
{
    dst[0] = 0;
    if (iso == NULL || iso[0] == 0) {
        return;
    }
    const char *t = strchr(iso, 'T');
    if (t == NULL) {
        t = strchr(iso, ' ');
    }
    t = (t && t[1]) ? t + 1 : iso;
    size_t len = strlen(t);
    if (len > 8) {
        len = 8;
    }
    memcpy(dst, t, len);
    dst[len] = 0;
}

/***********************************************************
********************** parsing (worker) ********************
***********************************************************/
static void __parse_direction(cJSON *root, TRADER_SNAP_T *s)
{
    const char *d = __jstr(root, "direction");
    if (d == NULL) {
        d = __jstr(root, "signal"); /* older API shape */
    }
    if (d == NULL || d[0] == 0) {
        return;
    }
    char low[8];
    size_t  n = strlen(d);
    if (n >= sizeof(low)) {
        n = sizeof(low) - 1;
    }
    for (size_t i = 0; i < n; i++) {
        low[i] = (char)tolower((unsigned char)d[i]);
    }
    low[n] = 0;
    if (0 == strcmp(low, "buy")) {
        __cp(low, sizeof(low), "long");
    } else if (0 == strcmp(low, "sell")) {
        __cp(low, sizeof(low), "short");
    }
    if (0 == strcmp(low, "long") || 0 == strcmp(low, "short") || 0 == strcmp(low, "flat")) {
        __cp(s->direction, sizeof(s->direction), low);
    }
}

static void __parse_signal(const char *json, TRADER_SNAP_T *s)
{
    cJSON *r = cJSON_Parse(json);
    if (r == NULL) {
        return;
    }
    double v;
    __parse_direction(r, s);
    if (__jnum(r, "last_price", &v) || __jnum(r, "price", &v)) {
        s->last_price = v;
        s->has_price  = 1;
    }
    if (__jnum(r, "entry", &v))          { s->entry = v;      s->has_entry = 1; }
    if (__jnum(r, "stop_loss", &v))      { s->stop_loss = v;  s->has_sl = 1;    }
    if (__jnum(r, "risk_reward", &v))    { s->risk_reward = v; s->has_rr = 1;   }
    if (__jnum(r, "confidence", &v))     { s->confidence = v; s->has_conf = 1;  }

    cJSON *tp = cJSON_GetObjectItem(r, "take_profits");
    if (tp && cJSON_IsArray(tp)) {
        cJSON *first = cJSON_GetArrayItem(tp, 0);
        if (first && cJSON_IsNumber(first)) {
            s->tp1    = first->valuedouble;
            s->has_tp = 1;
        }
    } else if (__jnum(r, "take_profit", &v)) {
        s->tp1    = v;
        s->has_tp = 1;
    }

    cJSON *ok = cJSON_GetObjectItem(r, "regime_ok");
    if (ok && cJSON_IsBool(ok)) {
        s->regime_ok = cJSON_IsTrue(ok) ? 1 : 0;
    }

    __cp(s->invalidation, sizeof(s->invalidation), __jstr(r, "invalidation_reason"));
    __cp(s->generated_at, sizeof(s->generated_at), __jstr(r, "generated_at"));

    cJSON *ind = cJSON_GetObjectItem(r, "indicators");
    if (ind && cJSON_IsObject(ind)) {
        if (__jnum(ind, "rsi14", &v))      { s->rsi14 = v;       s->has_rsi = 1;   }
        if (__jnum(ind, "macd_hist", &v))  { s->macd_hist = v;   s->has_macdh = 1; }
        if (__jnum(ind, "adx14", &v))      { s->adx14 = v;       s->has_adx = 1;   }
        if (__jnum(ind, "atr14", &v))      { s->atr14 = v;       s->has_atr = 1;   }
        if (__jnum(ind, "support", &v))    { s->support = v;     s->has_sup = 1;   }
        if (__jnum(ind, "resistance", &v)) { s->resistance = v;  s->has_res = 1;   }
    }
    cJSON_Delete(r);
}

static void __parse_candles(const char *json, TRADER_SNAP_T *s)
{
    cJSON *r = cJSON_Parse(json);
    if (r == NULL) {
        return;
    }
    cJSON *arr = cJSON_GetObjectItem(r, "c");
    if (arr && cJSON_IsArray(arr)) {
        CANDLE_T tmp[TRADER_MAX_CANDLES];
        int      n   = 0;
        cJSON   *it  = NULL;
        cJSON_ArrayForEach(it, arr)
        {
            if (n >= TRADER_MAX_CANDLES) {
                break;
            }
            double o, h, l, c;
            if (!__jnum(it, "o", &o) || !__jnum(it, "h", &h) ||
                !__jnum(it, "l", &l) || !__jnum(it, "c", &c)) {
                continue; /* skip malformed candle, keep the rest */
            }
            tmp[n].o = o;
            tmp[n].h = h;
            tmp[n].l = l;
            tmp[n].c = c;
            n++;
        }
        if (n > 0) {
            memcpy(s->candles, tmp, (size_t)n * sizeof(tmp[0]));
            s->n_candles  = n;
            s->candles_ok = 1;
        }
    }
    cJSON_Delete(r);
}

/***********************************************************
********************** network (worker) ********************
***********************************************************/
/* GET <path> from the Pi. Returns a tal_malloc'd NUL-terminated body, or NULL.
 * Caller tal_free()s it. Blocking - worker thread only.
 *
 * NOTE: http_client_request() allocates response->buffer and response->body
 * itself; http_client_free() owns the teardown. Do NOT pre-malloc resp.buffer
 * here and do NOT free resp.body ourselves (that would be a double free). */
static char *__http_get(const char *path)
{
    /* "Connection: close" — one fresh socket per request; a reused keep-alive
     * connection to a restarted service fails with SEND_FAULT (see app_brain.c). */
    http_client_header_t hdrs[] = {
        { .key = "Connection", .value = "close" },
    };
    http_client_request_t req = {
        .host          = TRADER_PI_HOST,
        .port          = TRADER_PI_PORT,
        .path          = path,
        .method        = "GET",
        .headers       = hdrs,
        .headers_count = 1,
        .body          = NULL,
        .body_length   = 0,
        .timeout_ms    = TRADER_HTTP_TIMEOUT,
        /* plain HTTP: cacert stays NULL and tls_no_verify stays false */
    };

    http_client_response_t resp = {0};
    char                  *out  = NULL;

    http_client_status_t st = http_client_request(&req, &resp);
    if (st == HTTP_CLIENT_SUCCESS && resp.status_code == 200 && resp.body && resp.body_length) {
        size_t nlen = resp.body_length;
        if (nlen > TRADER_MAX_BODY) {
            nlen = TRADER_MAX_BODY;
        }
        out = tal_malloc(nlen + 1);
        if (out) {
            memcpy(out, resp.body, nlen);
            out[nlen] = 0;
        }
    } else {
        PR_WARN("trader: GET %s failed (st=%d http=%u)", path, st, resp.status_code);
    }

    http_client_free(&resp);
    return out;
}

/* One full refresh cycle for symbol index `sym`. Worker thread only.
 * `with_candles` gates the slower /candles fetch (~every 30s, or right after
 * a symbol change). Starts from the worker's last-good state so a partial
 * failure degrades into "some fields are stale" instead of a blank screen. */
static void __do_poll(int sym, int with_candles)
{
    char path[128];

    s_work.online = 0; /* proven otherwise below */

    snprintf(path, sizeof(path), "/signal?symbol=%s&profile=aggressive&tf=%s",
             S_SYMBOLS[sym], TRADER_TF_QUERY);
    char *js = __http_get(path);
    if (js) {
        s_work.online = 1;
        __parse_signal(js, &s_work);
        tal_free(js);
    }

    if (with_candles) {
        snprintf(path, sizeof(path), "/candles?symbol=%s&tf=%s&n=%d",
                 S_SYMBOLS[sym], TRADER_TF_QUERY, TRADER_N_CANDLES);
        js = __http_get(path);
        if (js) {
            __parse_candles(js, &s_work);
            tal_free(js);
        } else {
            s_work.candles_ok = 0; /* hide the chart, keep the stats */
            s_work.n_candles  = 0;
        }
    }

    memcpy(&s_stage, &s_work, sizeof(s_stage));
    s_ready = 1;
}

static void __trader_worker(void *arg)
{
    (void)arg;
    int cur    = -1; /* forces a reset + candle fetch on the first cycle */
    int cycles = 0;
    for (;;) {
        int req = s_sym_req;
        if (req < 0 || req >= TRADER_N_SYMBOLS) {
            req = 0;
        }
        if (req != cur) {
            /* Symbol changed (or first run): reset the snapshot so no stale
             * XAUUSD numbers linger under a EURUSD label. */
            cur    = req;
            cycles = 0;
            memset(&s_work, 0, sizeof(s_work));
            __cp(s_work.symbol, sizeof(s_work.symbol), S_SYMBOLS[cur]);
            __cp(s_work.direction, sizeof(s_work.direction), "flat");
            memcpy(&s_stage, &s_work, sizeof(s_stage));
            s_ready = 1;
        }
        __do_poll(cur, cycles % TRADER_CANDLE_EVERY == 0);
        cycles++;
        /* Sleep in small steps so a symbol tap is picked up quickly. */
        for (int i = 0; i < TRADER_REFRESH_MS / TRADER_STEP_MS; i++) {
            if (s_sym_req != cur) {
                break;
            }
            tal_system_sleep(TRADER_STEP_MS);
        }
    }
}

/***********************************************************
********************** chart rendering *********************
***********************************************************/
/* Direct RGB565 pixel writes into the canvas buffer; LVGL thread only. */
static void __px(int x, int y, uint16_t c)
{
    if (s_cbuf && x >= 0 && x < CH_W && y >= 0 && y < CH_H) {
        s_cbuf[y * CH_W + x] = c;
    }
}

static void __fill_rect(int x, int y, int w, int h, uint16_t c)
{
    for (int j = y; j < y + h; j++) {
        for (int i = x; i < x + w; i++) {
            __px(i, j, c);
        }
    }
}

static void __hline(int y, int x0, int x1, uint16_t c, int dotted)
{
    for (int x = x0; x <= x1; x++) {
        if (!dotted || (x % 6) < 3) {
            __px(x, y, c);
        }
    }
}

/* Redraw the whole candle chart from s_snap. LVGL thread only. */
static void __draw_chart(void)
{
    if (s_cbuf == NULL || s_snap.n_candles == 0) {
        return;
    }
    const int n    = s_snap.n_candles;
    const int pw   = CH_W - CH_AX; /* plot width (right zone is the axis) */
    const int top  = 4, bot = CH_H - 5;

    __fill_rect(0, 0, CH_W, CH_H, PX565(COL_PANEL));

    /* price range across the visible candles */
    double lo = s_snap.candles[0].l, hi = s_snap.candles[0].h;
    for (int i = 1; i < n; i++) {
        if (s_snap.candles[i].l < lo) lo = s_snap.candles[i].l;
        if (s_snap.candles[i].h > hi) hi = s_snap.candles[i].h;
    }
    double pad = (hi - lo) * 0.06;
    if (pad <= 0.0) {
        pad = (hi > 0.0) ? hi * 0.001 : 1.0;
    }
    lo -= pad;
    hi += pad;
    const double span = hi - lo;

#define PRICE_Y(v) (bot - (int)(((v) - lo) / span * (double)(bot - top) + 0.5))

    /* horizontal grid hairlines */
    for (int g = 1; g <= 3; g++) {
        int y = top + (bot - top) * g / 4;
        __hline(y, 0, pw - 1, PX565(COL_LINE), 0);
    }

    /* candles: green/red bodies + wicks, oldest -> newest. The draw-in
     * animation caps how many are visible (range still spans all n, so the
     * scale doesn't jump while the sweep runs). */
    const int lim = (s_reveal >= 0 && s_reveal < n) ? s_reveal : n;
    const int slot   = (pw > n) ? pw / n : 1;
    int       body_w = slot - 2;
    if (body_w > 7) body_w = 7;
    if (body_w < 2) body_w = 2;
    for (int i = 0; i < lim; i++) {
        const CANDLE_T *cd = &s_snap.candles[i];
        int xc = i * slot + slot / 2;
        int x0 = xc - body_w / 2;
        int up = (cd->c >= cd->o);
        uint16_t col = PX565(up ? COL_BULL : COL_BEAR);
        int yh = PRICE_Y(cd->h);
        int yl = PRICE_Y(cd->l);
        for (int y = yh; y <= yl; y++) {
            __px(xc, y, col); /* wick */
        }
        int yo = PRICE_Y(cd->o);
        int yc = PRICE_Y(cd->c);
        int ytop = (yo < yc) ? yo : yc;
        int ybot = (yo > yc) ? yo : yc;
        if (ybot == ytop) {
            ybot++; /* doji: at least a 1px body */
        }
        __fill_rect(x0, ytop, body_w, ybot - ytop, col);
    }

    /* "Breathing" scanline: a dotted marker at the newest visible close,
     * blinked on/off by the 500 ms blink timer. */
    if (s_blink && lim > 0) {
        __hline(PRICE_Y(s_snap.candles[lim - 1].c), 0, pw - 1, PX565(COL_AMBER), 1);
    }

    /* support / resistance dotted levels (only when inside the range) */
    if (s_snap.has_sup && s_snap.support > lo && s_snap.support < hi) {
        __hline(PRICE_Y(s_snap.support), 0, pw - 1, PX565(COL_SUP), 1);
    }
    if (s_snap.has_res && s_snap.resistance > lo && s_snap.resistance < hi) {
        __hline(PRICE_Y(s_snap.resistance), 0, pw - 1, PX565(COL_RES), 1);
    }

    /* axis zone separator */
    for (int y = 0; y < CH_H; y++) {
        __px(pw, y, PX565(COL_LINE));
    }

#undef PRICE_Y

    char buf[24];
    __fmt_price(buf, sizeof(buf), s_snap.symbol, hi);
    lv_label_set_text(s_max_lbl, buf);
    __fmt_price(buf, sizeof(buf), s_snap.symbol, lo);
    lv_label_set_text(s_min_lbl, buf);

    lv_obj_invalidate(s_canvas);
}

/***********************************************************
********************* animation engine *********************
***********************************************************/
/* All animations run on the LVGL thread (lv_anim / lv_timer). Proper 2-arg
 * exec callbacks only - never cast a style setter to lv_anim_exec_xcb_t. */
static void __opa_anim_cb(void *obj, int32_t v)
{
    lv_obj_set_style_opa((lv_obj_t *)obj, (lv_opa_t)v, LV_PART_MAIN);
}

static void __bg_opa_anim_cb(void *obj, int32_t v)
{
    lv_obj_set_style_bg_opa((lv_obj_t *)obj, (lv_opa_t)v, LV_PART_MAIN);
}

static void __y_anim_cb(void *obj, int32_t v)
{
    lv_obj_set_y((lv_obj_t *)obj, v);
}

/* Chart draw-in: the sweep count lives in s_reveal; each step repaints the
 * canvas (direct RGB565 pixel writes are cheap). */
static void __reveal_anim_cb(void *obj, int32_t v)
{
    (void)obj;
    s_reveal = (int)v;
    __draw_chart();
}

static void __reveal_done_cb(lv_anim_t *a)
{
    (void)a;
    s_reveal = -1; /* draw all candles from now on */
    __draw_chart();
}

/* Start the left->right candle sweep for the current snapshot. */
static void __start_reveal(void)
{
    if (s_canvas == NULL || s_snap.n_candles == 0) {
        s_reveal = -1;
        __draw_chart();
        return;
    }
    lv_anim_delete(s_canvas, __reveal_anim_cb);
    s_reveal = 0;
    __draw_chart();

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_canvas);
    lv_anim_set_values(&a, 0, s_snap.n_candles);
    lv_anim_set_time(&a, 600);
    lv_anim_set_exec_cb(&a, __reveal_anim_cb);
    lv_anim_set_completed_cb(&a, __reveal_done_cb);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_start(&a);
}

/* Price tick: tint the hero price green/red until the next poll, fade a
 * matching bg flash out over ~300 ms, and pop the +/-% chip with a small
 * rise (a y-offset anim - no transform layers, cheap on the SPI display). */
static void __price_tick(int up)
{
    uint32_t c = up ? COL_BULL : COL_BEAR;
    lv_obj_set_style_text_color(s_price_lbl, lv_color_hex(c), LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_price_lbl, lv_color_hex(c), LV_PART_MAIN);
    lv_obj_set_style_radius(s_price_lbl, 6, LV_PART_MAIN);

    lv_anim_delete(s_price_lbl, __bg_opa_anim_cb);
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_price_lbl);
    lv_anim_set_values(&a, LV_OPA_40, LV_OPA_TRANSP);
    lv_anim_set_time(&a, 300);
    lv_anim_set_exec_cb(&a, __bg_opa_anim_cb);
    lv_anim_start(&a);

    lv_anim_delete(s_chg_lbl, __y_anim_cb);
    lv_anim_t b;
    lv_anim_init(&b);
    lv_anim_set_var(&b, s_chg_lbl);
    lv_anim_set_values(&b, CHG_Y + 4, CHG_Y);
    lv_anim_set_time(&b, 250);
    lv_anim_set_exec_cb(&b, __y_anim_cb);
    lv_anim_set_path_cb(&b, lv_anim_path_ease_out);
    lv_anim_start(&b);
}

/* Fade one object back in after a symbol swap (~150 ms). */
static void __fade_in(lv_obj_t *obj)
{
    if (obj == NULL) {
        return;
    }
    lv_anim_delete(obj, __opa_anim_cb);
    lv_obj_set_style_opa(obj, LV_OPA_TRANSP, LV_PART_MAIN);

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, obj);
    lv_anim_set_values(&a, LV_OPA_TRANSP, LV_OPA_COVER);
    lv_anim_set_time(&a, 150);
    lv_anim_set_exec_cb(&a, __opa_anim_cb);
    lv_anim_start(&a);
}

/* NO TRADE: slow attention pulse on the ladder strip (100% <-> 60%, 2 s). */
static void __ladder_pulse_start(void)
{
    if (s_ladder_pulsing || s_ladder == NULL) {
        return;
    }
    s_ladder_pulsing = 1;

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_ladder);
    lv_anim_set_values(&a, LV_OPA_COVER, LV_OPA_60);
    lv_anim_set_time(&a, 1000);
    lv_anim_set_playback_time(&a, 1000);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_exec_cb(&a, __opa_anim_cb);
    lv_anim_start(&a);
}

static void __ladder_pulse_stop(void)
{
    if (!s_ladder_pulsing || s_ladder == NULL) {
        return;
    }
    s_ladder_pulsing = 0;
    lv_anim_delete(s_ladder, __opa_anim_cb);
    lv_obj_set_style_opa(s_ladder, LV_OPA_COVER, LV_PART_MAIN);
}

/* Scanline blink: toggles the phase and repaints so the chart "breathes". */
static void __blink_cb(lv_timer_t *t)
{
    (void)t;
    if (s_canvas == NULL || s_snap.n_candles == 0) {
        return;
    }
    if (lv_obj_has_flag(s_canvas, LV_OBJ_FLAG_HIDDEN)) {
        return;
    }
    s_blink ^= 1;
    __draw_chart();
}

/***********************************************************
************************* painting *************************
***********************************************************/
/* "… switching to EURUSD …" state while the worker resets + re-fetches. */
static void __paint_loading(const char *sym)
{
    s_prev_price_ok = 0; /* don't flash the first tick of the new symbol */
    lv_label_set_text_fmt(s_sym_lbl, "%s \xC2\xB7 %s", sym, TRADER_TF_LABEL);
    lv_obj_set_style_text_color(s_sym_lbl, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_label_set_text(s_price_lbl, "--");
    lv_obj_set_style_text_color(s_price_lbl, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_label_set_text(s_chg_lbl, "loading...");
    lv_obj_set_style_text_color(s_chg_lbl, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_label_set_text(s_dir_lbl, "---");
    lv_obj_set_style_bg_color(s_dir_pill, lv_color_hex(COL_FLAT), LV_PART_MAIN);
    lv_label_set_text(s_chart_msg, "loading candles...");
    lv_obj_remove_flag(s_chart_msg, LV_OBJ_FLAG_HIDDEN);
    if (s_canvas) {
        lv_obj_add_flag(s_canvas, LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_add_flag(s_entry_lbl, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_sl_lbl, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_tp_lbl, LV_OBJ_FLAG_HIDDEN);
    lv_label_set_text(s_notrade_lbl, "switching symbol...");
    lv_obj_remove_flag(s_notrade_lbl, LV_OBJ_FLAG_HIDDEN);
    lv_label_set_text(s_rsi_val, "--");
    lv_label_set_text(s_macd_val, "--");
    lv_label_set_text(s_adx_val, "--");
    lv_label_set_text(s_atr_val, "--");
    lv_label_set_text(s_foot_lbl, "--:--:--");
}

static void __paint(void)
{
    /* Symbol tap pending? Show a loading state until the worker catches up. */
    int req = s_sym_req;
    if (req < 0 || req >= TRADER_N_SYMBOLS) {
        req = 0;
    }
    if (0 != strcmp(s_snap.symbol, S_SYMBOLS[req])) {
        __paint_loading(S_SYMBOLS[req]);
        return;
    }

    /* ---- top bar ---- */
    lv_label_set_text_fmt(s_sym_lbl, "%s \xC2\xB7 %s",
                          s_snap.symbol[0] ? s_snap.symbol : S_SYMBOLS[req], TRADER_TF_LABEL);
    lv_obj_set_style_text_color(s_sym_lbl, lv_color_hex(COL_TEXT), LV_PART_MAIN);
    if (s_snap.online) {
        lv_obj_set_style_bg_color(s_live_dot, lv_color_hex(COL_BULL), LV_PART_MAIN);
        lv_label_set_text(s_live_lbl, "LIVE");
        lv_obj_set_style_text_color(s_live_lbl, lv_color_hex(COL_BULL), LV_PART_MAIN);
    } else {
        lv_obj_set_style_bg_color(s_live_dot, lv_color_hex(COL_BEAR), LV_PART_MAIN);
        lv_label_set_text(s_live_lbl, "OFFLINE");
        lv_obj_set_style_text_color(s_live_lbl, lv_color_hex(COL_BEAR), LV_PART_MAIN);
    }

    /* ---- hero: price, change, direction pill ---- */
    char buf[32];
    if (s_snap.has_price) {
        __fmt_price(buf, sizeof(buf), s_snap.symbol, s_snap.last_price);
        lv_label_set_text(s_price_lbl, buf);
        if (s_prev_price_ok && s_snap.last_price != s_prev_price) {
            __price_tick(s_snap.last_price > s_prev_price);
        } else {
            lv_obj_set_style_text_color(s_price_lbl, lv_color_hex(COL_TEXT), LV_PART_MAIN);
        }
        s_prev_price    = s_snap.last_price;
        s_prev_price_ok = 1;
    } else {
        s_prev_price_ok = 0;
        lv_label_set_text(s_price_lbl, "--");
        lv_obj_set_style_text_color(s_price_lbl, lv_color_hex(COL_DIM), LV_PART_MAIN);
    }

    /* change vs the previous close of the candle series */
    if (s_snap.has_price && s_snap.n_candles > 0) {
        double base = (s_snap.n_candles >= 2) ? s_snap.candles[s_snap.n_candles - 2].c
                                              : s_snap.candles[s_snap.n_candles - 1].c;
        if (base > 0.0) {
            double chg = (s_snap.last_price - base) / base * 100.0;
            lv_label_set_text_fmt(s_chg_lbl, "%s%.2f%%", chg >= 0.0 ? "+" : "", chg);
            lv_obj_set_style_text_color(s_chg_lbl,
                                        lv_color_hex(chg >= 0.0 ? COL_BULL : COL_BEAR), LV_PART_MAIN);
        } else {
            lv_label_set_text(s_chg_lbl, "");
        }
    } else {
        lv_label_set_text(s_chg_lbl, "");
    }

    int is_long  = (0 == strcmp(s_snap.direction, "long"));
    int is_short = (0 == strcmp(s_snap.direction, "short"));
    lv_label_set_text(s_dir_lbl, is_long ? "LONG" : is_short ? "SHORT" : "FLAT");
    lv_obj_set_style_bg_color(s_dir_pill,
                              lv_color_hex(is_long ? COL_BULL : is_short ? COL_BEAR : COL_FLAT),
                              LV_PART_MAIN);

    /* ---- trade ladder ---- */
    int has_trade = (is_long || is_short) && s_snap.has_entry && s_snap.has_sl && s_snap.has_tp;
    if (has_trade) {
        char eb[20], sb[20], tb[20];
        __fmt_price(eb, sizeof(eb), s_snap.symbol, s_snap.entry);
        __fmt_price(sb, sizeof(sb), s_snap.symbol, s_snap.stop_loss);
        __fmt_price(tb, sizeof(tb), s_snap.symbol, s_snap.tp1);
        lv_label_set_text_fmt(s_entry_lbl, "ENTRY  %s", eb);
        lv_label_set_text_fmt(s_sl_lbl, "SL  %s", sb);
        lv_label_set_text_fmt(s_tp_lbl, "TP1  %s", tb);
        lv_obj_remove_flag(s_entry_lbl, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_sl_lbl, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_tp_lbl, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_notrade_lbl, LV_OBJ_FLAG_HIDDEN);
        __ladder_pulse_stop();
    } else {
        lv_label_set_text_fmt(s_notrade_lbl, "NO TRADE - %s",
                              s_snap.invalidation[0] ? s_snap.invalidation
                                                     : (s_snap.online ? "no edge right now"
                                                                      : "feed offline"));
        lv_obj_remove_flag(s_notrade_lbl, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_entry_lbl, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_sl_lbl, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_tp_lbl, LV_OBJ_FLAG_HIDDEN);
        __ladder_pulse_start();
    }

    /* ---- stat chips ---- */
    if (s_snap.has_rsi) {
        lv_label_set_text_fmt(s_rsi_val, "%.1f", s_snap.rsi14);
        uint32_t c = (s_snap.rsi14 < 30.0) ? COL_BULL : (s_snap.rsi14 > 70.0) ? COL_BEAR : COL_TEXT;
        lv_obj_set_style_text_color(s_rsi_val, lv_color_hex(c), LV_PART_MAIN);
    } else {
        lv_label_set_text(s_rsi_val, "--");
        lv_obj_set_style_text_color(s_rsi_val, lv_color_hex(COL_DIM), LV_PART_MAIN);
    }
    if (s_snap.has_macdh) {
        lv_label_set_text_fmt(s_macd_val, "%s%.3f", s_snap.macd_hist >= 0.0 ? "+" : "", s_snap.macd_hist);
        lv_obj_set_style_text_color(s_macd_val, lv_color_hex(COL_TEXT), LV_PART_MAIN);
    } else {
        lv_label_set_text(s_macd_val, "--");
        lv_obj_set_style_text_color(s_macd_val, lv_color_hex(COL_DIM), LV_PART_MAIN);
    }
    if (s_snap.has_adx) {
        lv_label_set_text_fmt(s_adx_val, "%.1f", s_snap.adx14);
        lv_obj_set_style_text_color(s_adx_val, lv_color_hex(COL_TEXT), LV_PART_MAIN);
    } else {
        lv_label_set_text(s_adx_val, "--");
        lv_obj_set_style_text_color(s_adx_val, lv_color_hex(COL_DIM), LV_PART_MAIN);
    }
    if (s_snap.has_atr) {
        lv_label_set_text_fmt(s_atr_val, "%.2f", s_snap.atr14);
        lv_obj_set_style_text_color(s_atr_val, lv_color_hex(COL_TEXT), LV_PART_MAIN);
    } else {
        lv_label_set_text(s_atr_val, "--");
        lv_obj_set_style_text_color(s_atr_val, lv_color_hex(COL_DIM), LV_PART_MAIN);
    }

    /* ---- chart ---- */
    if (s_snap.n_candles > 0 && s_cbuf && s_canvas) {
        lv_obj_add_flag(s_chart_msg, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_canvas, LV_OBJ_FLAG_HIDDEN);
        if (0 != strcmp(s_revealed_sym, s_snap.symbol)) {
            /* First candles for this symbol: sweep them in left->right. */
            __cp(s_revealed_sym, sizeof(s_revealed_sym), s_snap.symbol);
            __start_reveal();
        } else {
            __draw_chart();
        }
    } else {
        lv_label_set_text(s_chart_msg, s_snap.online ? "waiting for candle feed..."
                                                     : "candle feed offline");
        lv_obj_remove_flag(s_chart_msg, LV_OBJ_FLAG_HIDDEN);
        if (s_canvas) {
            lv_obj_add_flag(s_canvas, LV_OBJ_FLAG_HIDDEN);
        }
    }

    /* ---- footer ---- */
    char tbuf[12];
    __fmt_time(tbuf, sizeof(tbuf), s_snap.generated_at);
    lv_label_set_text(s_foot_lbl, tbuf[0] ? tbuf : "--:--:--");

    /* Symbol swap delivered its first snapshot: bring hero + chart back. */
    if (s_fading) {
        s_fading = 0;
        __fade_in(s_hero);
        __fade_in(s_panel);
    }
}

/***********************************************************
*********************** LVGL callbacks *********************
***********************************************************/
static void __poll_cb(lv_timer_t *t)
{
    (void)t;
    if (s_ready) {
        s_ready = 0;
        memcpy(&s_snap, &s_stage, sizeof(s_snap));
        __paint();
        return;
    }
    /* Symbol tap not yet picked up by the worker? Show the loading state. */
    int req = s_sym_req;
    if (req < 0 || req >= TRADER_N_SYMBOLS) {
        req = 0;
    }
    if (0 != strcmp(s_snap.symbol, S_SYMBOLS[req])) {
        __paint_loading(S_SYMBOLS[req]);
    }
}

/* Chart tap: cycle the symbol. LVGL context - fades the hero row + chart
 * out, then only bumps a volatile index (the worker owns the re-fetch); the
 * poll timer fades everything back in on the new symbol's first snapshot. */
static void __chart_tap_cb(lv_event_t *e)
{
    (void)e;
    s_fading = 1;

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_values(&a, LV_OPA_COVER, LV_OPA_TRANSP);
    lv_anim_set_time(&a, 150);
    lv_anim_set_exec_cb(&a, __opa_anim_cb);
    if (s_hero) {
        lv_anim_set_var(&a, s_hero);
        lv_anim_start(&a);
    }
    if (s_panel) {
        lv_anim_set_var(&a, s_panel);
        lv_anim_start(&a);
    }

    s_sym_req = (s_sym_req + 1) % TRADER_N_SYMBOLS;
}

/* Back to the apps grid. Uses the shell's lock-free request flag because a
 * click already runs inside LVGL with the display mutex held. */
static void __back_cb(lv_event_t *e)
{
    (void)e;
    kaleido_request_apps();
}

/***********************************************************
************************** start ***************************
***********************************************************/
/* Start the signal worker. Idempotent: called when the Trader facet is first
 * built, harmless if the worker is already running. */
static void __trader_start(void)
{
    if (s_worker == NULL) {
        THREAD_CFG_T cfg = {
            .thrdname   = "trader",
            .priority   = THREAD_PRIO_2,
            .stackDepth = 1024 * 8, /* HTTP + cJSON both want room */
        };
        if (OPRT_OK != tal_thread_create_and_start(&s_worker, NULL, NULL, __trader_worker, NULL, &cfg)) {
            s_worker = NULL;
            PR_ERR("trader: worker start failed - staying offline");
        }
    }
}

/***********************************************************
************************** build ***************************
***********************************************************/
/* One stat chip: dim label on the left, bold value on the right. */
static lv_obj_t *__build_chip(lv_obj_t *parent, int x, int y, const char *label, lv_obj_t **val_out)
{
    lv_obj_t *chip = lv_obj_create(parent);
    lv_obj_set_size(chip, 112, 30);
    lv_obj_set_pos(chip, x, y);
    lv_obj_set_style_bg_color(chip, lv_color_hex(COL_PANEL), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(chip, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(chip, 7, LV_PART_MAIN);
    lv_obj_set_style_border_width(chip, 1, LV_PART_MAIN);
    lv_obj_set_style_border_color(chip, lv_color_hex(COL_LINE), LV_PART_MAIN);
    lv_obj_set_style_pad_all(chip, 0, LV_PART_MAIN);
    lv_obj_remove_flag(chip, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *lab = lv_label_create(chip);
    lv_label_set_text(lab, label);
    lv_obj_set_style_text_color(lab, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_obj_align(lab, LV_ALIGN_LEFT_MID, 8, 0);

    *val_out = lv_label_create(chip);
    lv_label_set_text(*val_out, "--");
    lv_obj_set_style_text_color(*val_out, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_obj_align(*val_out, LV_ALIGN_RIGHT_MID, -8, 0);
    return chip;
}

static void __build(KALEIDO_APP_T *self, lv_obj_t *root)
{
    (void)self;

    /* Full-bleed terminal: no shell header, near-black teal backdrop. */
    lv_obj_set_style_bg_color(root, lv_color_hex(COL_BG), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(root, LV_OPA_COVER, LV_PART_MAIN);

    /* ---- top bar ---- */
    lv_obj_t *back = lv_obj_create(root);
    lv_obj_set_size(back, 34, 28);
    lv_obj_set_pos(back, 4, 5);
    lv_obj_set_style_bg_color(back, lv_color_hex(COL_PANEL), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(back, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(back, 8, LV_PART_MAIN);
    lv_obj_set_style_border_width(back, 0, LV_PART_MAIN);
    lv_obj_remove_flag(back, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(back, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(back, __back_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *bl = lv_label_create(back);
    lv_label_set_text(bl, LV_SYMBOL_LEFT);
    lv_obj_set_style_text_color(bl, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_obj_center(bl);

    s_sym_lbl = lv_label_create(root);
    lv_label_set_text(s_sym_lbl, "XAUUSD \xC2\xB7 " TRADER_TF_LABEL);
    lv_obj_set_style_text_font(s_sym_lbl, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_set_style_text_color(s_sym_lbl, lv_color_hex(COL_TEXT), LV_PART_MAIN);
    lv_obj_set_pos(s_sym_lbl, 46, 11);

    s_live_lbl = lv_label_create(root);
    lv_label_set_text(s_live_lbl, "LIVE");
    lv_obj_set_style_text_color(s_live_lbl, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_obj_align(s_live_lbl, LV_ALIGN_TOP_RIGHT, -22, 11);

    s_live_dot = lv_obj_create(root);
    lv_obj_set_size(s_live_dot, 10, 10);
    lv_obj_align(s_live_dot, LV_ALIGN_TOP_RIGHT, -6, 14);
    lv_obj_set_style_bg_color(s_live_dot, lv_color_hex(COL_BULL), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_live_dot, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(s_live_dot, LV_RADIUS_CIRCLE, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_live_dot, 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_live_dot, LV_OBJ_FLAG_SCROLLABLE);

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_live_dot);
    lv_anim_set_values(&a, LV_OPA_COVER, 60);
    lv_anim_set_time(&a, 700);
    lv_anim_set_playback_time(&a, 700);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_exec_cb(&a, __opa_anim_cb);
    lv_anim_start(&a);

    /* hairline under the top bar */
    lv_obj_t *hr = lv_obj_create(root);
    lv_obj_set_size(hr, KAL_W, 1);
    lv_obj_set_pos(hr, 0, 38);
    lv_obj_set_style_bg_color(hr, lv_color_hex(COL_LINE), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(hr, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(hr, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(hr, 0, LV_PART_MAIN);
    lv_obj_remove_flag(hr, LV_OBJ_FLAG_SCROLLABLE);

    /* ---- hero row (one container so a symbol switch can fade it) ---- */
    s_hero = lv_obj_create(root);
    lv_obj_set_size(s_hero, 464, 50);
    lv_obj_set_pos(s_hero, 8, 42);
    lv_obj_set_style_bg_opa(s_hero, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_hero, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_hero, 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_hero, LV_OBJ_FLAG_SCROLLABLE);

    s_price_lbl = lv_label_create(s_hero);
    lv_label_set_text(s_price_lbl, "--");
    lv_obj_set_style_text_font(s_price_lbl, &lv_font_montserrat_24, LV_PART_MAIN);
    lv_obj_set_style_text_color(s_price_lbl, lv_color_hex(COL_TEXT), LV_PART_MAIN);
    lv_obj_set_pos(s_price_lbl, 2, 2);

    s_chg_lbl = lv_label_create(s_hero);
    lv_label_set_text(s_chg_lbl, "");
    lv_obj_set_style_text_color(s_chg_lbl, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_obj_set_pos(s_chg_lbl, 4, CHG_Y);

    s_dir_pill = lv_obj_create(s_hero);
    lv_obj_set_size(s_dir_pill, 104, 34);
    lv_obj_align(s_dir_pill, LV_ALIGN_TOP_RIGHT, -2, 4);
    lv_obj_set_style_bg_color(s_dir_pill, lv_color_hex(COL_FLAT), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_dir_pill, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(s_dir_pill, LV_RADIUS_CIRCLE, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_dir_pill, 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_dir_pill, LV_OBJ_FLAG_SCROLLABLE);

    s_dir_lbl = lv_label_create(s_dir_pill);
    lv_label_set_text(s_dir_lbl, "FLAT");
    lv_obj_set_style_text_font(s_dir_lbl, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_set_style_text_color(s_dir_lbl, lv_color_hex(0xFFFFFF), LV_PART_MAIN);
    lv_obj_center(s_dir_lbl);

    /* ---- chart panel (tap = next symbol) ---- */
    lv_obj_t *panel = lv_obj_create(root);
    lv_obj_set_size(panel, 464, 140);
    lv_obj_set_pos(panel, 8, 92);
    lv_obj_set_style_bg_color(panel, lv_color_hex(COL_PANEL), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(panel, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(panel, 10, LV_PART_MAIN);
    lv_obj_set_style_border_width(panel, 1, LV_PART_MAIN);
    lv_obj_set_style_border_color(panel, lv_color_hex(COL_LINE), LV_PART_MAIN);
    lv_obj_set_style_pad_all(panel, 0, LV_PART_MAIN);
    lv_obj_remove_flag(panel, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(panel, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(panel, __chart_tap_cb, LV_EVENT_CLICKED, NULL);
    s_panel = panel;

    /* Candle canvas: one PSRAM buffer, pixels drawn in the LVGL timer.
     * lv_canvas clears CLICKABLE, so taps fall through to the panel. */
    s_cbuf = tal_psram_malloc((size_t)CH_W * CH_H * sizeof(uint16_t));
    if (s_cbuf) {
        memset(s_cbuf, 0, (size_t)CH_W * CH_H * sizeof(uint16_t));
        s_canvas = lv_canvas_create(panel);
        lv_canvas_set_buffer(s_canvas, s_cbuf, CH_W, CH_H, LV_COLOR_FORMAT_RGB565);
        lv_obj_set_pos(s_canvas, 12, 4);
    } else {
        PR_ERR("trader: canvas buffer alloc failed - chart disabled");
        s_canvas = NULL;
    }

    s_chart_msg = lv_label_create(panel);
    lv_label_set_text(s_chart_msg, "waiting for candle feed...");
    lv_obj_set_style_text_color(s_chart_msg, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_obj_center(s_chart_msg);

    s_max_lbl = lv_label_create(panel);
    lv_label_set_text(s_max_lbl, "");
    lv_obj_set_style_text_color(s_max_lbl, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_obj_align(s_max_lbl, LV_ALIGN_TOP_RIGHT, -8, 6);

    s_min_lbl = lv_label_create(panel);
    lv_label_set_text(s_min_lbl, "");
    lv_obj_set_style_text_color(s_min_lbl, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_obj_align(s_min_lbl, LV_ALIGN_BOTTOM_RIGHT, -8, -6);

    /* ---- trade ladder strip ---- */
    lv_obj_t *ladder = lv_obj_create(root);
    lv_obj_set_size(ladder, 464, 28);
    lv_obj_set_pos(ladder, 8, 238);
    lv_obj_set_style_bg_color(ladder, lv_color_hex(COL_PANEL), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(ladder, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(ladder, 8, LV_PART_MAIN);
    lv_obj_set_style_border_width(ladder, 1, LV_PART_MAIN);
    lv_obj_set_style_border_color(ladder, lv_color_hex(COL_LINE), LV_PART_MAIN);
    lv_obj_set_style_pad_all(ladder, 0, LV_PART_MAIN);
    lv_obj_remove_flag(ladder, LV_OBJ_FLAG_SCROLLABLE);
    s_ladder = ladder;

    s_entry_lbl = lv_label_create(ladder);
    lv_label_set_text(s_entry_lbl, "ENTRY  --");
    lv_obj_set_style_text_color(s_entry_lbl, lv_color_hex(COL_AMBER), LV_PART_MAIN);
    lv_obj_align(s_entry_lbl, LV_ALIGN_LEFT_MID, 12, 0);

    s_sl_lbl = lv_label_create(ladder);
    lv_label_set_text(s_sl_lbl, "SL  --");
    lv_obj_set_style_text_color(s_sl_lbl, lv_color_hex(COL_BEAR), LV_PART_MAIN);
    lv_obj_align(s_sl_lbl, LV_ALIGN_CENTER, 0, 0);

    s_tp_lbl = lv_label_create(ladder);
    lv_label_set_text(s_tp_lbl, "TP1  --");
    lv_obj_set_style_text_color(s_tp_lbl, lv_color_hex(COL_BULL), LV_PART_MAIN);
    lv_obj_align(s_tp_lbl, LV_ALIGN_RIGHT_MID, -12, 0);

    s_notrade_lbl = lv_label_create(ladder);
    lv_label_set_long_mode(s_notrade_lbl, LV_LABEL_LONG_DOT);
    lv_obj_set_width(s_notrade_lbl, 440);
    lv_label_set_text(s_notrade_lbl, "NO TRADE");
    lv_obj_set_style_text_color(s_notrade_lbl, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_obj_align(s_notrade_lbl, LV_ALIGN_LEFT_MID, 12, 0);
    lv_obj_add_flag(s_notrade_lbl, LV_OBJ_FLAG_HIDDEN);

    /* ---- stat chips ---- */
    __build_chip(root, 8,   272, "RSI",  &s_rsi_val);
    __build_chip(root, 124, 272, "MACD", &s_macd_val);
    __build_chip(root, 240, 272, "ADX",  &s_adx_val);
    __build_chip(root, 356, 272, "ATR",  &s_atr_val);

    /* ---- footer ---- */
    s_foot_lbl = lv_label_create(root);
    lv_label_set_text(s_foot_lbl, "--:--:--");
    lv_obj_set_style_text_color(s_foot_lbl, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_obj_set_pos(s_foot_lbl, 10, 304);

    lv_obj_t *brand = lv_label_create(root);
    lv_label_set_text(brand, "PANDA AI \xC2\xB7 paper");
    lv_obj_set_style_text_color(brand, lv_color_hex(COL_DIM), LV_PART_MAIN);
    lv_obj_align(brand, LV_ALIGN_BOTTOM_RIGHT, -10, -2);

    /* First paint from whatever we have (empty on the very first open). */
    __paint();

    /* ---- worker + poll plumbing (idempotent) ---- */
    __trader_start();

    lv_timer_create(__poll_cb, TRADER_POLL_MS, NULL);
    lv_timer_create(__blink_cb, 500, NULL); /* scanline blink: chart breathes */
}

/***********************************************************
************************* the facet ************************
***********************************************************/
KALEIDO_APP_T kaleido_app_trader = {
    .name  = "Trader",
    .track = "PANDA AI",
    .desc  = "A pocket trading terminal: live PandaAI signals, candles and levels from the OrangePi.",
    .glyph = LV_SYMBOL_CHARGE,
    .tint  = 0xF6C453,
    .build = __build,
};
