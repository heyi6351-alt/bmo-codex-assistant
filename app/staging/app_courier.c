/**
 * @file app_courier.c
 * @brief Courier facet (Photon track) — "give your agent a messaging identity".
 *
 * BMO can send notes to your phone. Tap a preset note (or say "send a message")
 * and it is pushed to ntfy.sh/<topic>, the same topic the Guardian alerts on, so
 * one phone subscription covers the whole device.
 *
 * Threading: clicks and voice only ENQUEUE. A worker thread owns every blocking
 * HTTP call. Results come back through a lock-free ring that an lv_timer drains
 * inside LVGL context (same pattern as kaleido.c's __ui_poll_cb).
 *
 * Offline: nothing ever fails silently. A send that cannot reach the network is
 * logged as "queued (offline)" and kept in a small outbox; the next successful
 * send flushes the outbox and the old rows flip themselves to "sent".
 */
#include "kaleido.h"

#include "tal_api.h"
#include "tal_kv.h"
#include "http_client_interface.h"

#include <string.h>
#include <stdio.h>

/***********************************************************
********************* user configuration *******************
***********************************************************/
#define COURIER_NTFY_TOPIC   "Lynx-Companion-Rayane"   /* same topic as app_guardian.c */
#define COURIER_NTFY_HOST    "ntfy.sh"
#define COURIER_NTFY_PORT    443
#define COURIER_HTTP_BUF     1536
#define COURIER_TIMEOUT_MS   8000
#define COURIER_KV_KEY       "kal_courier_cnt"

/* log / plumbing sizes */
#define COURIER_LOG_MAX      10   /* rows kept on screen (newest first) */
#define COURIER_RES_N        8    /* worker -> UI result ring */
#define COURIER_OUTBOX_N     4    /* offline retry buffer (worker-thread only) */

/* per-entry state */
#define CSTATE_SENDING       1
#define CSTATE_SENT          2
#define CSTATE_QUEUED        3

/***********************************************************
************************* presets **************************
***********************************************************/
typedef struct {
    const char *label; /* short text shown on the button + in the log */
    const char *body;  /* what actually lands on the phone */
    const char *tags;  /* ntfy "Tags" header (emoji shortcodes) */
} COURIER_PRESET_T;

static const COURIER_PRESET_T k_presets[] = {
    {"I'm thinking of you",  "BMO says: I'm thinking of you.",              "yellow_heart"},
    {"Call me when you can", "BMO says: call me when you can, no rush.",    "telephone"},
    {"All good here",        "BMO says: all good here, nothing to worry.",  "white_check_mark"},
    {"Good night",           "BMO says: good night! Sleep well.",           "crescent_moon"},
};
#define COURIER_PRESET_CNT ((int)(sizeof(k_presets) / sizeof(k_presets[0])))

/* stable click payloads (avoids int<->pointer casts in user_data) */
static const int k_idx[COURIER_PRESET_CNT] = {0, 1, 2, 3};

/***********************************************************
********************* worker plumbing **********************
***********************************************************/
typedef struct {
    uint32_t id;        /* matches a log row */
    char     body[160];
    char     tags[40];
} COURIER_MSG_T;

static QUEUE_HANDLE  s_queue  = NULL;
static THREAD_HANDLE s_worker = NULL;

/* worker -> UI result ring (single producer, single consumer, no locking) */
static volatile uint32_t s_res_id[COURIER_RES_N];
static volatile int32_t  s_res_state[COURIER_RES_N];
static volatile uint32_t s_res_w = 0, s_res_r = 0;
static volatile int32_t  s_total_sent = 0;   /* published by the worker, read by UI */
static volatile int32_t  s_total_dirty = 0;

static void __res_push(uint32_t id, int32_t state)
{
    uint32_t w = s_res_w;
    s_res_id[w % COURIER_RES_N]    = id;
    s_res_state[w % COURIER_RES_N] = state;
    s_res_w = w + 1;
}

/***********************************************************
*********************** UI state ***************************
***********************************************************/
typedef struct {
    uint32_t id;
    int      state;
    uint32_t ms;
    char     text[40];
} COURIER_LOG_T;

static COURIER_LOG_T s_log[COURIER_LOG_MAX];
static int           s_log_cnt = 0;
static uint32_t      s_next_id = 0;

static lv_obj_t *s_row[COURIER_LOG_MAX];
static lv_obj_t *s_row_msg[COURIER_LOG_MAX];
static lv_obj_t *s_row_when[COURIER_LOG_MAX];
static lv_obj_t *s_row_state[COURIER_LOG_MAX];
static lv_obj_t *s_status_lbl = NULL;
static lv_obj_t *s_empty_lbl  = NULL;
static int       s_built      = 0;

/***********************************************************
******************** network (worker) **********************
***********************************************************/
/* Blocking. Returns 1 on a 2xx from ntfy, 0 otherwise. */
static int __send_push(const char *body, const char *tags)
{
    http_client_header_t headers[] = {
        {.key = "Title",    .value = "\xF0\x9F\x93\xAB Note from BMO"},
        {.key = "Priority", .value = "default"},
        {.key = "Tags",     .value = tags},
    };

    http_client_request_t req = {
        .host          = COURIER_NTFY_HOST,
        .port          = COURIER_NTFY_PORT,
        .path          = "/" COURIER_NTFY_TOPIC,
        .method        = "POST",
        .headers       = headers,
        .headers_count = sizeof(headers) / sizeof(headers[0]),
        .body          = (const uint8_t *)body,
        .body_length   = strlen(body),
        .tls_no_verify = true,          /* demo: skip cert pinning */
        .timeout_ms    = COURIER_TIMEOUT_MS,
    };

    /* Do NOT pre-allocate resp.buffer: http_client_request() overwrites it with
     * its own internal block that http_client_free() frees. Pre-allocating leaks
     * + double-frees. Pass a zeroed struct and free exactly once. */
    http_client_response_t resp = {0};
    http_client_status_t st = http_client_request(&req, &resp);
    int ok = (st == HTTP_CLIENT_SUCCESS && resp.status_code >= 200 && resp.status_code < 300);
    PR_NOTICE("courier push: status=%d http=%u -> %s", st, resp.status_code, ok ? "sent" : "queued");

    http_client_free(&resp);
    return ok;
}

/* Persisted lifetime counter. Worker thread only — no cross-thread KV access. */
static void __count_load(void)
{
    uint8_t *val = NULL;
    size_t   len = 0;
    if (0 == tal_kv_get(COURIER_KV_KEY, &val, &len) && val != NULL) {
        if (len == sizeof(int32_t)) {
            int32_t n = 0;
            memcpy(&n, val, sizeof(n));
            if (n >= 0 && n < 1000000) {
                s_total_sent = n;
            }
        }
        tal_kv_free(val);
    }
    s_total_dirty = 1;
}

static void __count_bump(void)
{
    int32_t n = s_total_sent + 1;
    s_total_sent = n;
    s_total_dirty = 1;
    tal_kv_set(COURIER_KV_KEY, (const uint8_t *)&n, sizeof(n));
}

static void __courier_worker(void *arg)
{
    (void)arg;

    /* Offline outbox: touched by this thread only, so it needs no locking. */
    static COURIER_MSG_T outbox[COURIER_OUTBOX_N];
    static int           outbox_cnt = 0;

    COURIER_MSG_T msg;
    __count_load();

    for (;;) {
        memset(&msg, 0, sizeof(msg));
        if (OPRT_OK != tal_queue_fetch(s_queue, &msg, QUEUE_WAIT_FOREVER)) {
            continue;
        }

        if (__send_push(msg.body, msg.tags)) {
            __count_bump();
            __res_push(msg.id, CSTATE_SENT);

            /* Network is back: flush anything that was stranded offline. */
            int kept = 0;
            for (int i = 0; i < outbox_cnt; i++) {
                if (__send_push(outbox[i].body, outbox[i].tags)) {
                    __count_bump();
                    __res_push(outbox[i].id, CSTATE_SENT);
                } else {
                    outbox[kept++] = outbox[i];
                }
            }
            outbox_cnt = kept;
        } else {
            /* No Wi-Fi / no cloud: keep it, tell the UI it is queued. */
            if (outbox_cnt < COURIER_OUTBOX_N) {
                outbox[outbox_cnt++] = msg;
            } else {
                memmove(&outbox[0], &outbox[1], sizeof(outbox[0]) * (COURIER_OUTBOX_N - 1));
                outbox[COURIER_OUTBOX_N - 1] = msg;
            }
            __res_push(msg.id, CSTATE_QUEUED);
        }
    }
}

/***********************************************************
************************ UI helpers ************************
***********************************************************/
/* Case-insensitive substring test (ASCII). */
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

/* LVGL context only. Repaints every log row from s_log. */
static void __render_log(void)
{
    if (!s_built) {
        return;
    }
    for (int i = 0; i < COURIER_LOG_MAX; i++) {
        if (s_row[i] == NULL) {
            continue;
        }
        if (i >= s_log_cnt) {
            lv_obj_add_flag(s_row[i], LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        lv_obj_remove_flag(s_row[i], LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(s_row_msg[i], s_log[i].text);

        uint32_t secs = s_log[i].ms / 1000;
        lv_label_set_text_fmt(s_row_when[i], "%02u:%02u",
                              (unsigned)((secs / 60) % 100), (unsigned)(secs % 60));

        const char *txt;
        lv_color_t  col;
        switch (s_log[i].state) {
        case CSTATE_SENT:
            txt = LV_SYMBOL_OK " sent";
            col = KAL_COL_ACCENT;
            break;
        case CSTATE_QUEUED:
            txt = LV_SYMBOL_WARNING " queued";
            col = KAL_COL_ALERT;
            break;
        default:
            txt = LV_SYMBOL_UPLOAD " sending";
            col = KAL_COL_TEXT;
            break;
        }
        lv_label_set_text(s_row_state[i], txt);
        lv_obj_set_style_text_color(s_row_state[i], col, LV_PART_MAIN);
    }

    if (s_empty_lbl) {
        if (s_log_cnt > 0) {
            lv_obj_add_flag(s_empty_lbl, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_remove_flag(s_empty_lbl, LV_OBJ_FLAG_HIDDEN);
        }
    }
}

/* LVGL context only. Adds a "sending" row and hands the note to the worker. */
static void __enqueue(int idx)
{
    if (idx < 0 || idx >= COURIER_PRESET_CNT) {
        return;
    }

    uint32_t id = ++s_next_id;

    for (int i = COURIER_LOG_MAX - 1; i > 0; i--) {
        s_log[i] = s_log[i - 1];
    }
    memset(&s_log[0], 0, sizeof(s_log[0]));
    s_log[0].id    = id;
    s_log[0].state = CSTATE_SENDING;
    s_log[0].ms    = tal_system_get_millisecond();
    strncpy(s_log[0].text, k_presets[idx].label, sizeof(s_log[0].text) - 1);
    if (s_log_cnt < COURIER_LOG_MAX) {
        s_log_cnt++;
    }

    int posted = 0;
    if (s_queue != NULL) {
        COURIER_MSG_T m;
        memset(&m, 0, sizeof(m));
        m.id = id;
        strncpy(m.body, k_presets[idx].body, sizeof(m.body) - 1);
        strncpy(m.tags, k_presets[idx].tags, sizeof(m.tags) - 1);
        posted = (OPRT_OK == tal_queue_post(s_queue, &m, 0));
    }
    if (!posted) {
        /* no worker / queue full -> still visible, never a silent failure */
        s_log[0].state = CSTATE_QUEUED;
    }

    __render_log();
}

/***********************************************************
*********************** callbacks **************************
***********************************************************/
static void __preset_cb(lv_event_t *e)
{
    const int *idx = (const int *)lv_event_get_user_data(e);
    if (idx) {
        __enqueue(*idx);
    }
}

/* Drains worker results + refreshes the counter. LVGL context (lv_timer). */
static void __poll_cb(lv_timer_t *t)
{
    (void)t;

    /* Consumer fell behind: skip whatever the producer already overwrote. */
    if ((uint32_t)(s_res_w - s_res_r) > COURIER_RES_N) {
        s_res_r = s_res_w - COURIER_RES_N;
    }

    int changed = 0;
    while (s_res_r != s_res_w) {
        uint32_t slot  = s_res_r % COURIER_RES_N;
        uint32_t id    = s_res_id[slot];
        int32_t  state = s_res_state[slot];
        s_res_r++;

        for (int i = 0; i < s_log_cnt; i++) {
            if (s_log[i].id == id) {
                s_log[i].state = (int)state;
                changed = 1;
                break;
            }
        }
    }
    if (changed) {
        __render_log();
    }

    if (s_total_dirty && s_status_lbl) {
        s_total_dirty = 0;
        lv_label_set_text_fmt(s_status_lbl, LV_SYMBOL_WIFI "  ntfy.sh/%s   %d sent",
                              COURIER_NTFY_TOPIC, (int)s_total_sent);
    }
}

/***********************************************************
************************* build ****************************
***********************************************************/
static void __build_row(lv_obj_t *parent, int i)
{
    lv_obj_t *row = lv_obj_create(parent);
    lv_obj_set_size(row, 280, 50);
    lv_obj_set_style_bg_color(row, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(row, 10, LV_PART_MAIN);
    lv_obj_set_style_border_width(row, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(row, 6, LV_PART_MAIN);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(row, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *msg = lv_label_create(row);
    lv_label_set_long_mode(msg, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(msg, 170);
    lv_label_set_text(msg, "");
    lv_obj_set_style_text_color(msg, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(msg, LV_ALIGN_TOP_LEFT, 0, 0);

    lv_obj_t *when = lv_label_create(row);
    lv_label_set_text(when, "");
    lv_obj_set_style_text_color(when, KAL_COL_FACE, LV_PART_MAIN);
    lv_obj_align(when, LV_ALIGN_BOTTOM_LEFT, 0, 0);

    lv_obj_t *state = lv_label_create(row);
    lv_label_set_text(state, "");
    lv_obj_set_style_text_color(state, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(state, LV_ALIGN_RIGHT_MID, 0, 0);

    s_row[i]       = row;
    s_row_msg[i]   = msg;
    s_row_when[i]  = when;
    s_row_state[i] = state;
}

static void __build(KALEIDO_APP_T *self, lv_obj_t *root)
{
    (void)self;
    lv_obj_t *body = kaleido_header(root, "Courier");

    /* --- intro line --- */
    lv_obj_t *hint = lv_label_create(body);
    lv_label_set_long_mode(hint, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(hint, 296);
    lv_label_set_text(hint, "Tap a note and I'll send it to your phone. Or just say \"send a message\".");
    lv_obj_set_style_text_color(hint, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(hint, LV_ALIGN_TOP_LEFT, 0, 0);

    s_status_lbl = lv_label_create(body);
    lv_label_set_long_mode(s_status_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_status_lbl, 296);
    lv_label_set_text_fmt(s_status_lbl, LV_SYMBOL_WIFI "  ntfy.sh/%s   %d sent",
                          COURIER_NTFY_TOPIC, (int)s_total_sent);
    lv_obj_set_style_text_color(s_status_lbl, KAL_COL_FACE, LV_PART_MAIN);
    lv_obj_align(s_status_lbl, LV_ALIGN_TOP_LEFT, 0, 36);

    /* --- preset buttons --- */
    lv_obj_t *btns = lv_obj_create(body);
    lv_obj_set_size(btns, 300, 182);
    lv_obj_set_pos(btns, 0, 58);
    lv_obj_set_style_bg_opa(btns, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(btns, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(btns, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_row(btns, 6, LV_PART_MAIN);
    lv_obj_remove_flag(btns, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_flex_flow(btns, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(btns, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

    for (int i = 0; i < COURIER_PRESET_CNT; i++) {
        lv_obj_t *btn = lv_obj_create(btns);
        lv_obj_set_size(btn, 286, 40);
        lv_obj_set_style_bg_color(btn, KAL_COL_TILE, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(btn, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_radius(btn, 12, LV_PART_MAIN);
        lv_obj_set_style_border_width(btn, 0, LV_PART_MAIN);
        lv_obj_set_style_pad_all(btn, 4, LV_PART_MAIN);
        lv_obj_remove_flag(btn, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(btn, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_event_cb(btn, __preset_cb, LV_EVENT_CLICKED, (void *)&k_idx[i]);

        lv_obj_t *icon = lv_label_create(btn);
        lv_label_set_text(icon, LV_SYMBOL_ENVELOPE);
        lv_obj_set_style_text_color(icon, KAL_COL_INK, LV_PART_MAIN);
        lv_obj_align(icon, LV_ALIGN_LEFT_MID, 4, 0);

        lv_obj_t *lbl = lv_label_create(btn);
        lv_label_set_text(lbl, k_presets[i].label);
        lv_obj_set_style_text_color(lbl, KAL_COL_INK, LV_PART_MAIN);
        lv_obj_align(lbl, LV_ALIGN_LEFT_MID, 30, 0);
    }

    /* --- sent log --- */
    lv_obj_t *log_title = lv_label_create(body);
    lv_label_set_text(log_title, "Sent log");
    lv_obj_set_style_text_color(log_title, KAL_COL_ACCENT, LV_PART_MAIN);
    lv_obj_align(log_title, LV_ALIGN_TOP_LEFT, 0, 246);

    lv_obj_t *log = lv_obj_create(body);
    lv_obj_set_size(log, 300, 148);
    lv_obj_set_pos(log, 0, 268);
    lv_obj_set_style_bg_opa(log, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(log, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(log, 4, LV_PART_MAIN);
    lv_obj_set_style_pad_row(log, 6, LV_PART_MAIN);
    lv_obj_set_flex_flow(log, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(log, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

    s_empty_lbl = lv_label_create(log);
    lv_label_set_long_mode(s_empty_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_empty_lbl, 280);
    lv_label_set_text(s_empty_lbl, "Nothing sent yet. Notes you send show up here with their delivery status.");
    lv_obj_set_style_text_color(s_empty_lbl, KAL_COL_TEXT, LV_PART_MAIN);

    for (int i = 0; i < COURIER_LOG_MAX; i++) {
        __build_row(log, i);
    }

    s_built = 1;
    __render_log();

    /* --- worker plumbing (created once, lazily; non-blocking calls) --- */
    if (s_queue == NULL) {
        if (OPRT_OK != tal_queue_create_init(&s_queue, sizeof(COURIER_MSG_T), 6)) {
            s_queue = NULL;
            PR_ERR("courier: queue init failed — running in offline-only mode");
        }
    }
    if (s_queue != NULL && s_worker == NULL) {
        THREAD_CFG_T cfg = {
            .thrdname   = "courier",
            .priority   = THREAD_PRIO_2,
            .stackDepth = 1024 * 6,   /* TLS + HTTP needs a healthy stack */
        };
        if (OPRT_OK != tal_thread_create_and_start(&s_worker, NULL, NULL, __courier_worker, NULL, &cfg)) {
            s_worker = NULL;
            PR_ERR("courier: worker start failed — sends will queue offline");
        }
    }

    lv_timer_create(__poll_cb, 200, NULL);
    PR_NOTICE("courier ready (topic: %s)", COURIER_NTFY_TOPIC);
}

/***********************************************************
************************* voice ****************************
***********************************************************/
/* Called from LVGL context (kaleido.c holds the mutex around it). */
static void __on_voice(KALEIDO_APP_T *self, const char *text)
{
    (void)self;
    if (text == NULL || text[0] == 0) {
        return;
    }
    if (__ci_contains(text, "send a message") || __ci_contains(text, "send message") ||
        __ci_contains(text, "tell them")      || __ci_contains(text, "send a note")) {
        __enqueue(0);
    }
}

KALEIDO_APP_T kaleido_app_courier = {
    .name     = "Courier",
    .track    = "Photon",
    .desc     = "I can send messages to your phone for you.",
    .glyph    = LV_SYMBOL_ENVELOPE,
    .tint     = lv_color_hex(0xC79BF2),
    .build    = __build,
    .on_voice = __on_voice,
};
