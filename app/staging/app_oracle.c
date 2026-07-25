/**
 * @file app_oracle.c
 * @brief Oracle facet (Fintech track) - "the money whisperer".
 *
 * One big price, a sparkline of the recent values, and ONE plain-language
 * sentence that says what the number means to a human ("Bitcoin is up 2.1%
 * today - a calm, positive day."). A Refresh button re-asks the network.
 *
 * Design notes (the things that keep the board alive):
 *  - ALL networking happens in a worker thread (tal_thread + tal_queue), exactly
 *    like app_guardian.c. A button click only posts a job; it never blocks LVGL.
 *  - The worker publishes into a tiny lock-free mailbox (volatile fields + a
 *    sequence counter bumped last). An lv_timer created in build() picks the
 *    result up on the LVGL thread. No mutex is ever taken from LVGL context.
 *  - OFFLINE IS A FIRST-CLASS MODE. The screen opens in clearly-labelled DEMO
 *    mode with a live random-walk sparkline, so the demo works with no Wi-Fi and
 *    no cloud. If an earlier run cached a real quote it is shown as CACHED until
 *    a live one lands. Only a real HTTP 200 + parsed JSON earns the LIVE badge.
 *  - No %f anywhere. Embedded printf often ships without float support, so every
 *    number is formatted with integer math: price in whole USD, 24h change in
 *    tenths of a percent.
 *  - Chart writes go through lv_chart_set_next_value() only. In LVGL 9 the
 *    series is a ring buffer with a start_point; lv_chart_set_value_by_id()
 *    indexes the RAW array and would scramble the line once the ring has moved.
 */
#include "kaleido.h"

#include "tal_api.h"
#include "tal_kv.h"
#include "http_client_interface.h"
#include "cJSON.h"

#include <stdlib.h>
#include <string.h>

/***********************************************************
********************* user configuration *******************
***********************************************************/
#define ORA_HOST        "api.coingecko.com"
#define ORA_PORT        443
#define ORA_PATH        "/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
#define ORA_JSON_KEY    "bitcoin"
#define ORA_ASSET       "BITCOIN"
#define ORA_ASSET_NICE  "Bitcoin"
#define ORA_TIMEOUT_MS  8000

#define ORA_SPARK_N     32      /* sparkline points                         */
#define ORA_TICK_MS     250     /* UI tick                                  */
#define ORA_WALK_EVERY  4       /* fallback walk step: every 4 ticks (1 s)   */
#define ORA_LIVE_EVERY  96      /* auto refresh when live: 96 ticks (24 s)   */
#define ORA_RETRY_EVERY 48      /* auto retry when offline: 48 ticks (12 s)  */

#define ORA_KV_KEY      "kal_oracle_v1"
#define ORA_KV_MAGIC    0x4F52434Cu /* "ORCL" */

/* Where the displayed number came from. Drives the pill and the honesty text. */
typedef enum {
    ORA_ST_DEMO = 0,   /* synthetic, clearly labelled           */
    ORA_ST_CACHED,     /* last real quote, restored from flash  */
    ORA_ST_LIVE,       /* fetched from the network just now     */
} ORA_STATE_E;

typedef struct {
    uint32_t magic;
    int32_t  price_usd;      /* whole dollars            */
    int32_t  change_tenths;  /* 24h change * 10, signed  */
} ORA_CACHE_T;

typedef struct {
    int dummy; /* "go fetch" is the entire message */
} ORA_JOB_T;

/***********************************************************
*********************** module state ***********************
***********************************************************/
/* --- LVGL-owned: touched only from the LVGL thread --- */
static lv_obj_t   *s_price_lbl, *s_chg_lbl, *s_pill, *s_pill_lbl;
static lv_obj_t   *s_chart, *s_story_lbl, *s_note_lbl, *s_src_lbl;
static lv_obj_t   *s_btn, *s_btn_lbl;
static lv_chart_series_t *s_ser;
static lv_timer_t *s_tick;

static int32_t     s_hist[ORA_SPARK_N];  /* mirror of the chart, for min/max  */
static int32_t     s_price;              /* the big number on screen          */
static int32_t     s_walk;               /* value driving the fallback wiggle */
static int32_t     s_change_tenths;
static ORA_STATE_E s_state = ORA_ST_DEMO;
static uint32_t    s_ticks;
static uint32_t    s_seen_seq;
static int         s_seeded;
static uint32_t    s_last_ok_ms;         /* 0 = never had a live quote        */
static int         s_pill_shown = -1;    /* repaint guards (avoid 4 Hz redraw)*/
static int         s_btn_shown  = -1;
static int         s_age_shown  = -1;

/* --- lock-free mailbox: worker writes, the LVGL tick reads --- */
static volatile int32_t  s_pub_price;
static volatile int32_t  s_pub_tenths;
static volatile int      s_pub_state;
static volatile uint32_t s_pub_seq;      /* bumped LAST: publishes the set    */
static volatile int      s_busy;         /* a fetch is in flight              */
static volatile int      s_net_fail;     /* the last attempt failed           */

/* --- worker plumbing --- */
static QUEUE_HANDLE  s_queue  = NULL;
static THREAD_HANDLE s_worker = NULL;

/***********************************************************
********************** tiny utilities **********************
***********************************************************/
/* Case-insensitive substring (ASCII), same shape as kaleido.c's helper. */
static int __ci_contains(const char *hay, const char *needle)
{
    if (!hay || !needle || !needle[0]) {
        return 0;
    }
    for (const char *p = hay; *p; p++) {
        size_t i = 0;
        for (;;) {
            char a = p[i], b = needle[i];
            if (b == 0) return 1;
            if (a == 0) return 0;
            if (a >= 'A' && a <= 'Z') a += 32;
            if (b >= 'A' && b <= 'Z') b += 32;
            if (a != b) break;
            i++;
        }
    }
    return 0;
}

/* "$67,432" - integer only, so we never touch printf's float path. */
static void __fmt_money(int32_t v, char *out, size_t n)
{
    char digits[16];
    int  neg = (v < 0);
    long a   = neg ? -(long)v : (long)v;
    int  len, i, o = 0, grp;

    if (out == NULL || n == 0) {
        return;
    }
    snprintf(digits, sizeof(digits), "%ld", a);
    len = (int)strlen(digits);

    if (neg && o + 1 < (int)n) out[o++] = '-';
    if (o + 1 < (int)n)        out[o++] = '$';

    grp = len % 3;
    if (grp == 0) grp = 3;
    for (i = 0; i < len && o + 1 < (int)n; i++) {
        if (i == grp) {
            if (o + 1 < (int)n) out[o++] = ',';
            grp += 3;
        }
        if (o + 1 < (int)n) out[o++] = digits[i];
    }
    out[o] = 0;
}

/* Round a double to an int without pulling in math.h. */
static int32_t __round_i32(double d)
{
    return (int32_t)(d >= 0 ? d + 0.5 : d - 0.5);
}

/***********************************************************
********************* sparkline handling *******************
***********************************************************/
/* Rescale Y to whatever the mirror currently holds. */
static void __rescale(void)
{
    int32_t lo = s_hist[0], hi = s_hist[0], pad;
    int i;

    for (i = 1; i < ORA_SPARK_N; i++) {
        if (s_hist[i] < lo) lo = s_hist[i];
        if (s_hist[i] > hi) hi = s_hist[i];
    }
    pad = (hi - lo) / 6;
    if (pad < 1) {
        pad = (hi / 200) + 1;   /* a flat line still deserves some air */
    }
    if (s_chart) {
        lv_chart_set_range(s_chart, LV_CHART_AXIS_PRIMARY_Y, lo - pad, hi + pad);
    }
}

/* Append one sample: shift the mirror left, push onto the chart ring. */
static void __push_sample(int32_t v)
{
    memmove(&s_hist[0], &s_hist[1], sizeof(s_hist[0]) * (ORA_SPARK_N - 1));
    s_hist[ORA_SPARK_N - 1] = v;

    if (s_chart == NULL || s_ser == NULL) {
        return;
    }
    __rescale();
    lv_chart_set_next_value(s_chart, s_ser, v);
    lv_chart_refresh(s_chart);
}

/* Flat line at `v` across the whole sparkline (the very first paint). */
static void __seed_flat(int32_t v)
{
    int i;
    for (i = 0; i < ORA_SPARK_N; i++) {
        s_hist[i] = v;
    }
    if (s_chart && s_ser) {
        lv_chart_set_all_value(s_chart, s_ser, v);  /* also resets start_point */
        __rescale();
        lv_chart_refresh(s_chart);
    }
}

/* Reconstruct a plausible 24h shape from price + 24h change so the first live
 * quote does not land on a dead flat line. Labelled "24h shape" in the UI.
 * Written with set_next_value only, so the ring order stays correct. */
static void __seed_from_change(int32_t price, int32_t tenths)
{
    double now   = (double)price;
    double ratio = 1.0 + ((double)tenths / 1000.0);
    double then  = (ratio > 0.05) ? (now / ratio) : now;
    int i;

    for (i = 0; i < ORA_SPARK_N; i++) {
        double f = (double)i / (double)(ORA_SPARK_N - 1);
        s_hist[i] = __round_i32(then + (now - then) * f);
    }
    if (s_chart && s_ser) {
        __rescale();
        for (i = 0; i < ORA_SPARK_N; i++) {
            lv_chart_set_next_value(s_chart, s_ser, s_hist[i]);
        }
        lv_chart_refresh(s_chart);
    }
}

/* Fallback wiggle: +/- ~0.15%, so the offline sparkline is never dead. */
static int32_t __walk_step(int32_t v)
{
    int32_t span = (v / 600) + 1;
    int32_t d    = (rand() % (2 * span + 1)) - span;
    int32_t nv   = v + d;
    if (nv < 10) nv = 10;
    return nv;
}

/***********************************************************
*********************** the one sentence *******************
***********************************************************/
static void __story_text(char *out, size_t n, ORA_STATE_E st, int32_t t)
{
    const char *lead = (st == ORA_ST_LIVE)   ? ""
                     : (st == ORA_ST_CACHED) ? "Last real quote I saved: "
                                             : "Practice numbers (no network): ";
    int32_t a = (t < 0) ? -t : t;
    int     w = (int)(a / 10), f = (int)(a % 10);

    if (t > 30) {
        snprintf(out, n, "%s%s is up %d.%d%% today - a strong push. Good day, but big moves swing both ways.",
                 lead, ORA_ASSET_NICE, w, f);
    } else if (t > 10) {
        snprintf(out, n, "%s%s is up %d.%d%% today - a calm, positive day. Nothing to do.",
                 lead, ORA_ASSET_NICE, w, f);
    } else if (t >= -10) {
        snprintf(out, n, "%s%s barely moved (%s%d.%d%%) - a quiet week. Boring is fine.",
                 lead, ORA_ASSET_NICE, (t < 0 ? "-" : "+"), w, f);
    } else if (t >= -30) {
        snprintf(out, n, "%s%s is down %d.%d%% today - a soft dip. A drop this size is normal.",
                 lead, ORA_ASSET_NICE, w, f);
    } else {
        snprintf(out, n, "%s%s is down %d.%d%% today - a rough day. Deep breaths, no panic buttons.",
                 lead, ORA_ASSET_NICE, w, f);
    }
}

/***********************************************************
************************ UI painting ***********************
***********************************************************/
/* Only repaints when the pill actually changes (the tick runs at 4 Hz). */
static void __paint_pill(void)
{
    int key = (s_busy ? 100 : 0) + (int)s_state;
    lv_color_t  c;
    const char *txt;

    if (s_pill == NULL || key == s_pill_shown) {
        return;
    }
    s_pill_shown = key;

    if (s_busy) {
        c = KAL_COL_ACCENT;
        txt = LV_SYMBOL_REFRESH " ASKING";
    } else if (s_state == ORA_ST_LIVE) {
        c = lv_color_hex(0x7BD389);
        txt = "LIVE";
    } else if (s_state == ORA_ST_CACHED) {
        c = KAL_COL_ACCENT;
        txt = "CACHED";
    } else {
        c = lv_color_hex(0xF29CA3);
        txt = "DEMO DATA";
    }
    lv_obj_set_style_bg_color(s_pill, c, LV_PART_MAIN);
    lv_label_set_text(s_pill_lbl, txt);
}

static void __paint_price_only(void)
{
    char money[24];
    __fmt_money(s_price, money, sizeof(money));
    lv_label_set_text(s_price_lbl, money);
}

static void __paint_numbers(void)
{
    char    story[192];
    int32_t a = (s_change_tenths < 0) ? -s_change_tenths : s_change_tenths;

    __paint_price_only();

    lv_label_set_text_fmt(s_chg_lbl, "%s %d.%d%%  (24h)",
                          (s_change_tenths > 0 ? LV_SYMBOL_UP
                           : s_change_tenths < 0 ? LV_SYMBOL_DOWN : "="),
                          (int)(a / 10), (int)(a % 10));
    lv_obj_set_style_text_color(s_chg_lbl,
                                (s_change_tenths > 0)   ? lv_color_hex(0x7BD389)
                                : (s_change_tenths < 0) ? lv_color_hex(0xF29CA3)
                                                        : KAL_COL_TEXT,
                                LV_PART_MAIN);

    __story_text(story, sizeof(story), s_state, s_change_tenths);
    lv_label_set_text(s_story_lbl, story);

    if (s_state == ORA_ST_LIVE) {
        lv_label_set_text(s_note_lbl, "Line: 24h shape, then one dot per live check.");
    } else if (s_state == ORA_ST_CACHED) {
        lv_label_set_text(s_note_lbl, "No network. Number is the last real price I saved;\nthe line below is only an illustration.");
    } else {
        lv_label_set_text(s_note_lbl, "Offline demo mode - these are NOT real prices.\nEverything on this screen still works.");
    }
    s_age_shown = -1;   /* force the "checked ..." line to repaint */
    __paint_pill();
}

/* "checked 12s ago" / "never reached the network" - repaints at most 1 Hz. */
static void __paint_age(void)
{
    int secs;

    if (s_src_lbl == NULL) {
        return;
    }
    if (s_last_ok_ms == 0) {
        if (s_age_shown == -2) {
            return;
        }
        s_age_shown = -2;
        lv_label_set_text(s_src_lbl, s_net_fail ? "Could not reach CoinGecko yet - tap REFRESH."
                                                : "Asking CoinGecko for a real price...");
        return;
    }
    secs = (int)((tal_system_get_millisecond() - s_last_ok_ms) / 1000);
    if (secs == s_age_shown) {
        return;
    }
    s_age_shown = secs;
    if (secs < 120) {
        lv_label_set_text_fmt(s_src_lbl, "CoinGecko - checked %ds ago.", secs);
    } else {
        lv_label_set_text_fmt(s_src_lbl, "CoinGecko - checked %dmin ago.", secs / 60);
    }
}

/***********************************************************
********************* worker (network) *********************
***********************************************************/
/* Persist the last good quote so a cold, offline boot shows something real. */
static void __cache_save(int32_t price, int32_t tenths)
{
    ORA_CACHE_T c;
    c.magic = ORA_KV_MAGIC;
    c.price_usd = price;
    c.change_tenths = tenths;
    tal_kv_set(ORA_KV_KEY, (const uint8_t *)&c, sizeof(c));
}

static int __cache_load(int32_t *price, int32_t *tenths)
{
    uint8_t *val = NULL;
    size_t   len = 0;
    int      ok  = 0;

    if (OPRT_OK != tal_kv_get(ORA_KV_KEY, &val, &len)) {
        return 0;
    }
    if (val && len == sizeof(ORA_CACHE_T)) {
        ORA_CACHE_T c;
        memcpy(&c, val, sizeof(c));
        if (c.magic == ORA_KV_MAGIC && c.price_usd > 0) {
            *price  = c.price_usd;
            *tenths = c.change_tenths;
            ok = 1;
        }
    }
    if (val) {
        tal_kv_free(val);
    }
    return ok;
}

static void __publish(int32_t price, int32_t tenths, ORA_STATE_E st)
{
    s_pub_price  = price;
    s_pub_tenths = tenths;
    s_pub_state  = (int)st;
    s_pub_seq++;   /* bumped LAST, so the tick only ever reads a complete set */
}

/* Blocking HTTPS GET + cJSON parse. Worker thread ONLY. 1 on success. */
static int __fetch_quote(int32_t *price, int32_t *tenths)
{
    http_client_header_t headers[] = {
        {.key = "Accept",     .value = "application/json"},
        {.key = "User-Agent", .value = "Kaleidoscope/1.0 (T5AI)"},
    };
    http_client_request_t req = {
        .host          = ORA_HOST,
        .port          = ORA_PORT,
        .path          = ORA_PATH,
        .method        = "GET",
        .headers       = headers,
        .headers_count = sizeof(headers) / sizeof(headers[0]),
        .body          = NULL,
        .body_length   = 0,
        .tls_no_verify = true,        /* demo board: no cert pinning */
        .timeout_ms    = ORA_TIMEOUT_MS,
    };
    /* Do NOT pre-allocate resp.buffer: http_client_request() overwrites .buffer
     * and .body with its own allocations, and http_client_free() frees BOTH.
     * Pre-allocating leaks; tal_free(resp.buffer) afterwards is a double free. */
    http_client_response_t resp = {0};
    http_client_status_t   st;
    char   json[384];
    size_t cp;
    cJSON *root = NULL, *coin = NULL, *usd = NULL, *chg = NULL;
    int    ok = 0;

    st = http_client_request(&req, &resp);
    if (st != HTTP_CLIENT_SUCCESS || resp.status_code != 200 ||
        resp.body == NULL || resp.body_length == 0) {
        PR_WARN("oracle: fetch failed st=%d http=%u", (int)st, resp.status_code);
        http_client_free(&resp);
        return 0;
    }

    /* The library's body buffer is not guaranteed NUL-terminated - copy it. */
    cp = resp.body_length;
    if (cp > sizeof(json) - 1) {
        cp = sizeof(json) - 1;
    }
    memcpy(json, resp.body, cp);
    json[cp] = 0;
    http_client_free(&resp);

    root = cJSON_Parse(json);
    if (root == NULL) {
        PR_WARN("oracle: could not parse the quote json");
        return 0;
    }
    coin = cJSON_GetObjectItem(root, ORA_JSON_KEY);
    if (coin) {
        usd = cJSON_GetObjectItem(coin, "usd");
        chg = cJSON_GetObjectItem(coin, "usd_24h_change");
    }
    if (usd && cJSON_IsNumber(usd) && usd->valuedouble > 0) {
        *price  = __round_i32(usd->valuedouble);
        *tenths = (chg && cJSON_IsNumber(chg)) ? __round_i32(chg->valuedouble * 10.0) : 0;
        if (*tenths >  9999) *tenths =  9999;
        if (*tenths < -9999) *tenths = -9999;
        ok = 1;
    }
    cJSON_Delete(root);
    return ok;
}

static void __oracle_worker(void *arg)
{
    (void)arg;
    ORA_JOB_T job;
    int first = 1;

    for (;;) {
        memset(&job, 0, sizeof(job));
        if (OPRT_OK != tal_queue_fetch(s_queue, &job, QUEUE_WAIT_FOREVER)) {
            continue;
        }

        /* On the first job, surface any cached real quote right away, so an
         * offline boot shows a real number instead of only practice data. */
        if (first) {
            int32_t cp = 0, ct = 0;
            first = 0;
            if (__cache_load(&cp, &ct)) {
                __publish(cp, ct, ORA_ST_CACHED);
            }
        }

        int32_t price = 0, tenths = 0;
        if (__fetch_quote(&price, &tenths)) {
            s_net_fail = 0;
            __cache_save(price, tenths);
            __publish(price, tenths, ORA_ST_LIVE);
            PR_NOTICE("oracle: live %ld usd, %ld tenths", (long)price, (long)tenths);
        } else {
            s_net_fail = 1;
            PR_WARN("oracle: staying on fallback data");
        }
        s_busy = 0;
    }
}

/* Post a fetch job. Safe from LVGL context: tal_queue_post with a 0 timeout
 * never blocks and never touches the LVGL mutex. */
static void __request_refresh(void)
{
    ORA_JOB_T job = {0};
    if (s_queue == NULL || s_busy) {
        return;
    }
    if (OPRT_OK == tal_queue_post(s_queue, &job, 0)) {
        s_busy = 1;
        __paint_pill();
    }
}

/***********************************************************
************************ UI callbacks **********************
***********************************************************/
static void __refresh_cb(lv_event_t *e)
{
    (void)e;
    __request_refresh();
    if (s_busy) {
        s_btn_shown = 1;
        lv_label_set_text(s_btn_lbl, LV_SYMBOL_REFRESH "  CHECKING...");
    }
}

static void __tick_cb(lv_timer_t *t)
{
    (void)t;
    s_ticks++;

    /* 1) Apply whatever the worker published (lock-free hand-off). */
    if (s_pub_seq != s_seen_seq) {
        s_seen_seq = s_pub_seq;
        int32_t     p  = s_pub_price;
        int32_t     ch = s_pub_tenths;
        ORA_STATE_E st = (ORA_STATE_E)s_pub_state;

        if (p > 0) {
            int was_synthetic = (s_state != ORA_ST_LIVE);
            s_price         = p;
            s_walk          = p;
            s_change_tenths = ch;
            s_state         = st;
            if (st == ORA_ST_LIVE) {
                s_last_ok_ms = tal_system_get_millisecond();
                if (s_last_ok_ms == 0) {
                    s_last_ok_ms = 1;   /* 0 means "never" */
                }
                if (was_synthetic) {
                    __seed_from_change(p, ch); /* first real quote: real shape */
                } else {
                    __push_sample(p);
                }
            } else {
                __seed_flat(p);
            }
            __paint_numbers();
        }
    }

    /* 2) Not live: keep the sparkline breathing so the demo is never dead.
     *    In DEMO the big number moves too; in CACHED the big number stays
     *    pinned to the real saved price and only the line is illustrative. */
    if (s_state != ORA_ST_LIVE && (s_ticks % ORA_WALK_EVERY) == 0) {
        s_walk = __walk_step(s_walk);
        __push_sample(s_walk);
        if (s_state == ORA_ST_DEMO) {
            s_price = s_walk;
            __paint_price_only();
        }
    }

    /* 3) Auto refresh when live, auto retry when not. */
    {
        uint32_t every = (s_state == ORA_ST_LIVE) ? ORA_LIVE_EVERY : ORA_RETRY_EVERY;
        if (!s_busy && (s_ticks % every) == 0) {
            __request_refresh();
        }
    }

    /* 4) Chrome. Both guarded, so this does not repaint at 4 Hz. */
    if (!s_busy && s_btn_shown != 0) {
        s_btn_shown = 0;
        lv_label_set_text(s_btn_lbl, LV_SYMBOL_REFRESH "  REFRESH");
    }
    __paint_pill();
    __paint_age();
}

static void __on_voice(KALEIDO_APP_T *self, const char *text)
{
    (void)self;
    if (text == NULL) {
        return;
    }
    if (__ci_contains(text, "price")  || __ci_contains(text, "bitcoin") ||
        __ci_contains(text, "market") || __ci_contains(text, "btc")     ||
        __ci_contains(text, "crypto") || __ci_contains(text, "how much")) {
        /* We are in LVGL context here (kaleido.c holds the mutex around this
         * call), so: plain lv_* plus a non-blocking queue post. Nothing else. */
        __request_refresh();
        if (s_btn_lbl) {
            s_btn_shown = 1;
            lv_label_set_text(s_btn_lbl, LV_SYMBOL_REFRESH "  ON IT...");
        }
    }
}

static void __on_enter(KALEIDO_APP_T *self)
{
    (void)self;
    __request_refresh();
}

/***********************************************************
************************** build ***************************
***********************************************************/
static void __start_worker_once(void)
{
    THREAD_CFG_T cfg = {
        .thrdname   = "oracle",
        .priority   = THREAD_PRIO_2,
        .stackDepth = 1024 * 6,   /* TLS handshake + cJSON need room */
    };
    if (s_worker != NULL) {
        return;
    }
    if (OPRT_OK != tal_queue_create_init(&s_queue, sizeof(ORA_JOB_T), 4)) {
        s_queue = NULL;
        PR_ERR("oracle: queue init failed - staying in offline mode");
        return;
    }
    if (OPRT_OK != tal_thread_create_and_start(&s_worker, NULL, NULL, __oracle_worker, NULL, &cfg)) {
        s_worker = NULL;
        PR_ERR("oracle: worker start failed - staying in offline mode");
    }
}

static void __build(KALEIDO_APP_T *self, lv_obj_t *root)
{
    (void)self;
    lv_obj_t *body = kaleido_header(root, "Oracle");

    if (!s_seeded) {
        srand((unsigned)tal_system_get_millisecond());
        s_seeded = 1;
    }

    /* ---- asset name ---- */
    lv_obj_t *coin = lv_label_create(body);
    lv_label_set_text(coin, ORA_ASSET "  /  USD");
    lv_obj_set_style_text_color(coin, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(coin, LV_ALIGN_TOP_LEFT, 0, 2);

    /* ---- status pill: LIVE / CACHED / DEMO DATA / ASKING ---- */
    s_pill = lv_obj_create(body);
    lv_obj_set_size(s_pill, 106, 24);
    lv_obj_align(s_pill, LV_ALIGN_TOP_RIGHT, 0, -2);
    lv_obj_set_style_radius(s_pill, 12, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_pill, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_pill, 0, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_pill, lv_color_hex(0xF29CA3), LV_PART_MAIN);
    lv_obj_remove_flag(s_pill, LV_OBJ_FLAG_SCROLLABLE);
    s_pill_lbl = lv_label_create(s_pill);
    lv_label_set_text(s_pill_lbl, "DEMO DATA");
    lv_obj_set_style_text_color(s_pill_lbl, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_center(s_pill_lbl);

    /* ---- the big number ---- */
    s_price_lbl = lv_label_create(body);
    lv_label_set_text(s_price_lbl, "$--");
    lv_obj_set_style_text_color(s_price_lbl, KAL_COL_TEXT, LV_PART_MAIN);
#if LV_FONT_MONTSERRAT_24
    lv_obj_set_style_text_font(s_price_lbl, &lv_font_montserrat_24, LV_PART_MAIN);
#endif
    lv_obj_align(s_price_lbl, LV_ALIGN_TOP_LEFT, 0, 24);

    /* ---- 24h change ---- */
    s_chg_lbl = lv_label_create(body);
    lv_label_set_text(s_chg_lbl, "= 0.0%  (24h)");
    lv_obj_set_style_text_color(s_chg_lbl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(s_chg_lbl, LV_ALIGN_TOP_LEFT, 0, 60);

    /* ---- sparkline ---- */
    s_chart = lv_chart_create(body);
    lv_obj_set_size(s_chart, 296, 116);
    lv_obj_align(s_chart, LV_ALIGN_TOP_LEFT, 0, 86);
    lv_chart_set_type(s_chart, LV_CHART_TYPE_LINE);
    lv_chart_set_point_count(s_chart, ORA_SPARK_N);
    lv_chart_set_update_mode(s_chart, LV_CHART_UPDATE_MODE_SHIFT);
    lv_chart_set_div_line_count(s_chart, 3, 0);
    lv_obj_set_style_bg_color(s_chart, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_chart, LV_OPA_40, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_chart, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(s_chart, 10, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_chart, 6, LV_PART_MAIN);
    lv_obj_set_style_line_color(s_chart, KAL_COL_FACE, LV_PART_MAIN); /* div lines */
    lv_obj_set_style_line_width(s_chart, 1, LV_PART_MAIN);
    lv_obj_set_style_line_width(s_chart, 3, LV_PART_ITEMS);           /* the series */
    /* Hide the per-point dots: in LVGL 9 the point size is INDICATOR w/h. */
    lv_obj_set_style_width(s_chart, 0, LV_PART_INDICATOR);
    lv_obj_set_style_height(s_chart, 0, LV_PART_INDICATOR);
    lv_obj_remove_flag(s_chart, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(s_chart, LV_OBJ_FLAG_CLICKABLE);
    s_ser = lv_chart_add_series(s_chart, KAL_COL_ACCENT, LV_CHART_AXIS_PRIMARY_Y);

    /* ---- the one plain-language sentence ---- */
    s_story_lbl = lv_label_create(body);
    lv_label_set_long_mode(s_story_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_story_lbl, 296);
    lv_label_set_text(s_story_lbl, "Warming up... I will say what the number means in plain words.");
    lv_obj_set_style_text_color(s_story_lbl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(s_story_lbl, LV_ALIGN_TOP_LEFT, 0, 212);

    /* ---- honesty lines ---- */
    s_note_lbl = lv_label_create(body);
    lv_label_set_long_mode(s_note_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_note_lbl, 296);
    lv_label_set_text(s_note_lbl, "Offline demo mode - these are NOT real prices.");
    lv_obj_set_style_text_color(s_note_lbl, KAL_COL_ACCENT, LV_PART_MAIN);
    lv_obj_align(s_note_lbl, LV_ALIGN_TOP_LEFT, 0, 286);

    s_src_lbl = lv_label_create(body);
    lv_label_set_long_mode(s_src_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_src_lbl, 296);
    lv_label_set_text(s_src_lbl, "Asking CoinGecko for a real price...");
    lv_obj_set_style_text_color(s_src_lbl, KAL_COL_FACE, LV_PART_MAIN);
    lv_obj_align(s_src_lbl, LV_ALIGN_TOP_LEFT, 0, 330);

    /* ---- refresh button ---- */
    s_btn = lv_obj_create(body);
    lv_obj_set_size(s_btn, 296, 52);
    lv_obj_align(s_btn, LV_ALIGN_BOTTOM_MID, 0, -4);
    lv_obj_set_style_bg_color(s_btn, KAL_COL_ACCENT, LV_PART_MAIN);
    lv_obj_set_style_radius(s_btn, 14, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_btn, 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_btn, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(s_btn, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_btn, __refresh_cb, LV_EVENT_CLICKED, NULL);
    s_btn_lbl = lv_label_create(s_btn);
    lv_label_set_text(s_btn_lbl, LV_SYMBOL_REFRESH "  REFRESH");
    lv_obj_set_style_text_color(s_btn_lbl, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_center(s_btn_lbl);

    /* ---- initial state: offline-safe, honest, and already moving ---- */
    s_price         = 64000 + (rand() % 6000);   /* plausible practice number */
    s_walk          = s_price;
    s_change_tenths = (rand() % 61) - 30;        /* -3.0% .. +3.0%            */
    s_state         = ORA_ST_DEMO;
    __seed_flat(s_price);
    __paint_numbers();
    __paint_age();

    /* ---- worker + UI tick ---- */
    __start_worker_once();
    s_tick = lv_timer_create(__tick_cb, ORA_TICK_MS, NULL);
    __request_refresh();
}

KALEIDO_APP_T kaleido_app_oracle = {
    .name     = "Oracle",
    .track    = "Fintech",
    .desc     = "I watch the market and explain it in one plain sentence.",
    .glyph    = LV_SYMBOL_UP,
    .tint     = lv_color_hex(0x7BD389),
    .build    = __build,
    .enter    = __on_enter,
    .on_voice = __on_voice,
};
