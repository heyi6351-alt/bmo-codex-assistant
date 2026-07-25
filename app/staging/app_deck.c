/**
 * @file app_deck.c
 * @brief Deck facet - the bridge to the second board.
 *
 * BMO has a body (this T5AI-Board: face, mics, speaker, touch) and a brain (an
 * Orange Pi 3B on the same Wi-Fi running the "Armada" Go daemon). Deck is the
 * window into that brain: it polls Armada's plain-HTTP REST API and shows what
 * the brain is doing right now and what it got up to overnight.
 *
 *   GET /v1/status  -> {"version","uptime","brain","tasks","by_state"}
 *   GET /v1/tasks   -> [ {"id","name","kind","runtime","state","runs","last_exit"} ]
 *   GET /v1/digest  -> {"since","ran","failed","raw","summary"}
 *
 * Threading: every byte of network I/O happens on the "deck" worker thread. The
 * worker fills a staging snapshot and raises a volatile flag; an lv_timer created
 * in build() notices the flag, copies the snapshot and repaints. No locks are
 * taken from either side (see kaleido.c for the same pattern).
 *
 * Offline: the last good snapshot is persisted to KV, so a cold boot with no Pi
 * (or no Wi-Fi at all) still shows a populated, believable screen. The very first
 * boot falls back to baked-in demo data. The demo never dies.
 *
 * To wire it up: add `extern KALEIDO_APP_T kaleido_app_deck;` to kaleido.h and
 * `kaleido_register(&kaleido_app_deck);` in kaleido_start(), then add this file
 * to the app CMakeLists source list.
 */
#include "kaleido.h"

#include "tal_api.h"
#include "tal_kv.h"
#include "http_client_interface.h"
#include "cJSON.h"

#include <string.h>
#include <stdio.h>

/***********************************************************
*********************** EDIT ME ****************************
***********************************************************/
/* >>> EDIT ME <<< the Orange Pi 3B's LAN address and the Armada REST port.
 * Plain HTTP on purpose: no TLS, no cert, no cloud round-trip. */
#define DECK_PI_HOST "192.168.1.50"
#define DECK_PI_PORT 8099

/***********************************************************
********************* tunables / limits ********************
***********************************************************/
#define DECK_HTTP_TIMEOUT_MS 2500  /* keep the offline path snappy on stage */
#define DECK_MAX_BODY        3072  /* biggest JSON body we will copy out     */
#define DECK_MAX_TASKS       12
#define DECK_POLL_MS         200   /* UI poll timer                          */
#define DECK_KV_KEY          "kal_deck_v1"
#define DECK_SNAP_MAGIC      0xDEC00001u

/***********************************************************
************************ data model ************************
***********************************************************/
typedef struct {
    char name[28];
    char kind[14];
    char runtime[14];
    char state[14];
    int  runs;
    int  last_exit;
} DECK_TASK_T;

typedef struct {
    uint32_t magic;
    int      have_data;  /* 1 once we have ever seen the brain (or demo seed) */
    int      online;     /* 1 = last refresh actually reached the Pi          */
    char     version[16];
    char     uptime[24];
    char     brain[24];
    int      tasks;
    char     by_state[40];
    char     dg_since[24];
    int      dg_ran;
    int      dg_failed;
    char     dg_summary[320];
    int      task_cnt;
    DECK_TASK_T task[DECK_MAX_TASKS];
} DECK_SNAP_T;

typedef struct {
    int what; /* 1 = full refresh */
} DECK_CMD_T;

/***********************************************************
*********************** module state ***********************
***********************************************************/
/* s_snap is touched only by the LVGL thread; s_stage only by the worker.
 * s_stage_ready is the one-way handoff between them. */
static DECK_SNAP_T s_snap;
static DECK_SNAP_T s_stage;

static volatile int s_stage_ready = 0; /* worker -> UI: a new snapshot is ready */
static volatile int s_busy        = 0; /* worker -> UI: a refresh is in flight  */
static int          s_busy_shown  = -1;
static int          s_flash       = 0; /* digest panel highlight countdown      */

static QUEUE_HANDLE  s_queue  = NULL;
static THREAD_HANDLE s_worker = NULL;

static lv_obj_t *s_status, *s_status_lbl, *s_stats_lbl;
static lv_obj_t *s_dg_panel, *s_dg_meta, *s_dg_text;
static lv_obj_t *s_list, *s_btn_lbl;

/***********************************************************
************************* helpers **************************
***********************************************************/
/* Case-insensitive substring test (ASCII), same as the shell's. */
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

static int __jint(cJSON *o, const char *k, int dflt)
{
    cJSON *i = cJSON_GetObjectItem(o, k);
    if (i && cJSON_IsNumber(i)) {
        return i->valueint;
    }
    return dflt;
}

/* "uptime" may come back as a pretty string or as raw seconds. Accept both. */
static void __fmt_uptime(cJSON *root, char *dst, size_t n)
{
    cJSON *i = cJSON_GetObjectItem(root, "uptime");
    if (i && cJSON_IsString(i) && i->valuestring) {
        __cp(dst, n, i->valuestring);
        return;
    }
    if (i && cJSON_IsNumber(i)) {
        long s = (long)i->valuedouble;
        if (s < 0) s = 0;
        long d = s / 86400, h = (s % 86400) / 3600, m = (s % 3600) / 60;
        if (d)      snprintf(dst, n, "up %ldd %02ldh", d, h);
        else if (h) snprintf(dst, n, "up %ldh %02ldm", h, m);
        else        snprintf(dst, n, "up %ldm", m);
        return;
    }
    __cp(dst, n, "up ?");
}

/* {"running":3,"idle":2} -> "3 running, 2 idle" */
static void __fmt_by_state(cJSON *root, char *dst, size_t n)
{
    dst[0] = 0;
    cJSON *bs = cJSON_GetObjectItem(root, "by_state");
    if (bs == NULL || !cJSON_IsObject(bs)) {
        return;
    }
    size_t used = 0;
    for (cJSON *c = bs->child; c != NULL; c = c->next) {
        if (c->string == NULL || !cJSON_IsNumber(c)) {
            continue;
        }
        int w = snprintf(dst + used, n - used, "%s%d %s", used ? ", " : "", c->valueint, c->string);
        if (w <= 0 || (size_t)w >= n - used) {
            dst[n - 1] = 0;
            return;
        }
        used += (size_t)w;
    }
}

static lv_color_t __state_col(const char *s)
{
    if (__ci_contains(s, "run"))                                  return lv_color_hex(0x7BD389);
    if (__ci_contains(s, "fail") || __ci_contains(s, "err"))      return KAL_COL_ALERT;
    if (__ci_contains(s, "done") || __ci_contains(s, "ok") ||
        __ci_contains(s, "succ"))                                 return KAL_COL_TILE;
    return lv_color_hex(0x6FB3F2);
}

/***********************************************************
*********************** cache (KV) *************************
***********************************************************/
static void __seed_demo(DECK_SNAP_T *s)
{
    memset(s, 0, sizeof(*s));
    s->magic     = DECK_SNAP_MAGIC;
    s->have_data = 1;
    s->online    = 0;
    __cp(s->version,  sizeof(s->version),  "armada 0.4.1");
    __cp(s->uptime,   sizeof(s->uptime),   "up 4d 02h");
    __cp(s->brain,    sizeof(s->brain),    "orangepi-3b");
    __cp(s->by_state, sizeof(s->by_state), "2 running, 3 idle, 1 failed");
    s->tasks = 6;

    __cp(s->dg_since,   sizeof(s->dg_since), "last 12h");
    s->dg_ran    = 41;
    s->dg_failed = 1;
    __cp(s->dg_summary, sizeof(s->dg_summary),
         "Quiet night. Backups and the feed crawler finished clean. "
         "One task (cert-renew) failed once at 03:12 and passed on retry. "
         "Nothing needs you right now.");

    static const DECK_TASK_T demo[] = {
        {"nightly-backup",  "cron",   "shell",  "done",    128, 0},
        {"feed-crawler",    "svc",    "go",     "running", 902, 0},
        {"cert-renew",      "cron",   "shell",  "failed",   14, 1},
        {"metrics-push",    "svc",    "go",     "running", 411, 0},
        {"log-rotate",      "cron",   "shell",  "idle",     30, 0},
        {"model-warm",      "oneshot","python", "idle",      3, 0},
    };
    s->task_cnt = (int)(sizeof(demo) / sizeof(demo[0]));
    if (s->task_cnt > DECK_MAX_TASKS) {
        s->task_cnt = DECK_MAX_TASKS;
    }
    memcpy(s->task, demo, sizeof(DECK_TASK_T) * (size_t)s->task_cnt);
}

static void __cache_load(void)
{
    uint8_t *val = NULL;
    size_t   len = 0;

    if (OPRT_OK == tal_kv_get(DECK_KV_KEY, &val, &len) && val != NULL) {
        if (len == sizeof(DECK_SNAP_T) && ((DECK_SNAP_T *)val)->magic == DECK_SNAP_MAGIC) {
            memcpy(&s_snap, val, sizeof(DECK_SNAP_T));
            s_snap.online = 0; /* nothing is proven until the first refresh */
            tal_kv_free(val);
            PR_NOTICE("deck: restored cached brain snapshot (%d tasks)", s_snap.task_cnt);
            return;
        }
        tal_kv_free(val);
    }
    __seed_demo(&s_snap);
    PR_NOTICE("deck: no cache, using demo snapshot");
}

static void __cache_save(const DECK_SNAP_T *s)
{
    if (OPRT_OK != tal_kv_set(DECK_KV_KEY, (const uint8_t *)s, sizeof(DECK_SNAP_T))) {
        PR_WARN("deck: cache save failed");
    }
}

/***********************************************************
********************** network (worker) ********************
***********************************************************/
/* GET <path> from the Pi. Returns a tal_malloc'd NUL-terminated body, or NULL.
 * Caller tal_free()s it. Blocking - worker thread only.
 *
 * NOTE: http_client_request() allocates response->buffer and response->body
 * itself and overwrites whatever we put there, so we pass a zeroed response and
 * let http_client_free() own the teardown. Do NOT pre-malloc resp.buffer here. */
static char *__http_get(const char *path)
{
    http_client_request_t req = {
        .host          = DECK_PI_HOST,
        .port          = DECK_PI_PORT,
        .path          = path,
        .method        = "GET",
        .headers       = NULL,
        .headers_count = 0,
        .body          = NULL,
        .body_length   = 0,
        .timeout_ms    = DECK_HTTP_TIMEOUT_MS,
        /* plain HTTP: cacert stays NULL and tls_no_verify stays false, which is
         * what makes the transport come up as TCP instead of TLS. */
    };

    http_client_response_t resp = {0};
    char                  *out  = NULL;

    http_client_status_t st = http_client_request(&req, &resp);
    if (st == HTTP_CLIENT_SUCCESS && resp.status_code == 200 && resp.body && resp.body_length) {
        size_t n = resp.body_length;
        if (n > DECK_MAX_BODY) {
            n = DECK_MAX_BODY;
        }
        out = tal_malloc(n + 1);
        if (out) {
            memcpy(out, resp.body, n);
            out[n] = 0;
        }
    } else {
        PR_WARN("deck: GET %s failed (st=%d http=%u)", path, st, resp.status_code);
    }

    http_client_free(&resp);
    return out;
}

static void __parse_status(const char *json, DECK_SNAP_T *s)
{
    cJSON *r = cJSON_Parse(json);
    if (r == NULL) {
        return;
    }
    __cp(s->version, sizeof(s->version), __jstr(r, "version"));
    __cp(s->brain,   sizeof(s->brain),   __jstr(r, "brain"));
    __fmt_uptime(r, s->uptime, sizeof(s->uptime));
    __fmt_by_state(r, s->by_state, sizeof(s->by_state));
    s->tasks = __jint(r, "tasks", s->tasks);
    cJSON_Delete(r);
}

static void __parse_tasks(const char *json, DECK_SNAP_T *s)
{
    cJSON *r = cJSON_Parse(json);
    if (r == NULL) {
        return;
    }
    if (!cJSON_IsArray(r)) {
        cJSON_Delete(r);
        return;
    }

    int n = cJSON_GetArraySize(r);
    if (n > DECK_MAX_TASKS) {
        n = DECK_MAX_TASKS;
    }
    memset(s->task, 0, sizeof(s->task));
    s->task_cnt = 0;

    for (int i = 0; i < n; i++) {
        cJSON *t = cJSON_GetArrayItem(r, i);
        if (t == NULL || !cJSON_IsObject(t)) {
            continue;
        }
        DECK_TASK_T *d = &s->task[s->task_cnt];
        const char  *nm = __jstr(t, "name");
        __cp(d->name,    sizeof(d->name),    nm ? nm : __jstr(t, "id"));
        __cp(d->kind,    sizeof(d->kind),    __jstr(t, "kind"));
        __cp(d->runtime, sizeof(d->runtime), __jstr(t, "runtime"));
        __cp(d->state,   sizeof(d->state),   __jstr(t, "state"));
        d->runs      = __jint(t, "runs", 0);
        d->last_exit = __jint(t, "last_exit", 0);
        if (d->name[0] == 0) {
            __cp(d->name, sizeof(d->name), "(unnamed)");
        }
        s->task_cnt++;
    }
    if (s->task_cnt > 0) {
        s->tasks = s->task_cnt;
    }
    cJSON_Delete(r);
}

static void __parse_digest(const char *json, DECK_SNAP_T *s)
{
    cJSON *r = cJSON_Parse(json);
    if (r == NULL) {
        return;
    }
    __cp(s->dg_since, sizeof(s->dg_since), __jstr(r, "since"));
    s->dg_ran    = __jint(r, "ran", 0);
    s->dg_failed = __jint(r, "failed", 0);

    const char *sum = __jstr(r, "summary");
    if (sum == NULL || sum[0] == 0) {
        sum = __jstr(r, "raw"); /* fall back to the unsummarised text */
    }
    if (sum && sum[0]) {
        __cp(s->dg_summary, sizeof(s->dg_summary), sum);
    }
    cJSON_Delete(r);
}

static void __do_refresh(void)
{
    /* Start from the last known good picture so a partial failure degrades into
     * "some fields are stale" instead of "the screen went blank". */
    memcpy(&s_stage, &s_snap, sizeof(s_stage));
    s_stage.magic = DECK_SNAP_MAGIC;

    char *js = __http_get("/v1/status");
    if (js == NULL) {
        /* Brain unreachable. Keep every cached value, just flip the light. */
        s_stage.online = 0;
        s_stage_ready  = 1;
        return;
    }
    s_stage.online    = 1;
    s_stage.have_data = 1;
    __parse_status(js, &s_stage);
    tal_free(js);

    js = __http_get("/v1/tasks");
    if (js) {
        __parse_tasks(js, &s_stage);
        tal_free(js);
    }

    js = __http_get("/v1/digest");
    if (js) {
        __parse_digest(js, &s_stage);
        tal_free(js);
    }

    __cache_save(&s_stage);
    s_stage_ready = 1;
}

static void __deck_worker(void *arg)
{
    (void)arg;
    DECK_CMD_T cmd;
    for (;;) {
        memset(&cmd, 0, sizeof(cmd));
        if (OPRT_OK != tal_queue_fetch(s_queue, &cmd, QUEUE_WAIT_FOREVER)) {
            continue;
        }
        s_busy = 1;
        __do_refresh();
        s_busy = 0;
    }
}

/* Safe from LVGL context: only posts to a queue, never blocks, never locks. */
static void __request_refresh(void)
{
    if (s_queue == NULL || s_busy) {
        return;
    }
    DECK_CMD_T c = {.what = 1};
    tal_queue_post(s_queue, &c, 0);
}

/***********************************************************
************************* painting *************************
***********************************************************/
static void __paint_status(void)
{
    if (s_status_lbl == NULL) {
        return;
    }
    if (s_busy) {
        lv_obj_set_style_bg_color(s_status, KAL_COL_TILE, LV_PART_MAIN);
        lv_label_set_text(s_status_lbl, LV_SYMBOL_REFRESH "  Reaching the brain...");
        lv_obj_set_style_text_color(s_status_lbl, KAL_COL_INK, LV_PART_MAIN);
    } else if (s_snap.online) {
        lv_obj_set_style_bg_color(s_status, lv_color_hex(0x7BD389), LV_PART_MAIN);
        lv_label_set_text_fmt(s_status_lbl, LV_SYMBOL_OK "  Bridge online  %s", DECK_PI_HOST);
        lv_obj_set_style_text_color(s_status_lbl, KAL_COL_INK, LV_PART_MAIN);
    } else {
        lv_obj_set_style_bg_color(s_status, KAL_COL_ALERT, LV_PART_MAIN);
        lv_label_set_text(s_status_lbl, LV_SYMBOL_WARNING "  Brain offline - showing last known");
        lv_obj_set_style_text_color(s_status_lbl, KAL_COL_TEXT, LV_PART_MAIN);
    }
}

static void __add_row(const DECK_TASK_T *t)
{
    lv_obj_t *row = lv_obj_create(s_list);
    lv_obj_set_size(row, 272, 46);
    lv_obj_set_style_bg_color(row, KAL_COL_BG, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(row, 10, LV_PART_MAIN);
    lv_obj_set_style_border_width(row, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(row, 7, LV_PART_MAIN);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *dot = lv_obj_create(row);
    lv_obj_set_size(dot, 10, 10);
    lv_obj_align(dot, LV_ALIGN_LEFT_MID, 0, 0);
    lv_obj_set_style_radius(dot, 5, LV_PART_MAIN);
    lv_obj_set_style_bg_color(dot, __state_col(t->state), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(dot, 0, LV_PART_MAIN);
    lv_obj_remove_flag(dot, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *nm = lv_label_create(row);
    lv_label_set_text(nm, t->name);
    lv_obj_set_style_text_color(nm, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(nm, LV_ALIGN_TOP_LEFT, 18, -1);

    lv_obj_t *sub = lv_label_create(row);
    lv_label_set_text_fmt(sub, "%s / %s / %d runs / exit %d",
                          t->kind[0]    ? t->kind    : "task",
                          t->runtime[0] ? t->runtime : "-",
                          t->runs, t->last_exit);
    lv_obj_set_style_text_color(sub, KAL_COL_TILE, LV_PART_MAIN);
    lv_obj_align(sub, LV_ALIGN_BOTTOM_LEFT, 18, 1);
}

static void __paint(void)
{
    __paint_status();

    lv_label_set_text_fmt(s_stats_lbl, "%s   %s\n%d tasks   %s",
                          s_snap.brain[0]   ? s_snap.brain   : "orangepi-3b",
                          s_snap.version[0] ? s_snap.version : "armada ?",
                          s_snap.tasks,
                          s_snap.by_state[0] ? s_snap.by_state : (s_snap.online ? "" : "(cached)"));

    lv_label_set_text_fmt(s_dg_meta, "since %s   %d ran   %d failed",
                          s_snap.dg_since[0] ? s_snap.dg_since : "?",
                          s_snap.dg_ran, s_snap.dg_failed);
    lv_label_set_text(s_dg_text, s_snap.dg_summary[0]
                                     ? s_snap.dg_summary
                                     : "No digest yet. Tap Refresh once the brain is on the network.");

    /* Rebuild the task list. Cheap: at most DECK_MAX_TASKS rows. */
    lv_obj_clean(s_list);
    if (s_snap.task_cnt == 0) {
        lv_obj_t *empty = lv_label_create(s_list);
        lv_label_set_long_mode(empty, LV_LABEL_LONG_WRAP);
        lv_obj_set_width(empty, 268);
        lv_label_set_text(empty, "No tasks reported.");
        lv_obj_set_style_text_color(empty, KAL_COL_TEXT, LV_PART_MAIN);
    } else {
        for (int i = 0; i < s_snap.task_cnt && i < DECK_MAX_TASKS; i++) {
            __add_row(&s_snap.task[i]);
        }
    }
}

/***********************************************************
*********************** LVGL callbacks *********************
***********************************************************/
static void __poll_cb(lv_timer_t *t)
{
    (void)t;

    if (s_stage_ready) {
        s_stage_ready = 0;
        memcpy(&s_snap, &s_stage, sizeof(s_snap));
        __paint();
    }

    if (s_busy != s_busy_shown) {
        s_busy_shown = s_busy;
        __paint_status();
        lv_label_set_text(s_btn_lbl, s_busy ? "SYNCING..." : LV_SYMBOL_REFRESH "  REFRESH");
    }

    if (s_flash > 0) {
        s_flash--;
        if (s_flash == 0) {
            lv_obj_set_style_border_width(s_dg_panel, 0, LV_PART_MAIN);
        }
    }
}

static void __refresh_cb(lv_event_t *e)
{
    (void)e;
    __request_refresh(); /* never blocks, never touches the LVGL mutex */
}

/***********************************************************
************************** build ***************************
***********************************************************/
static void __build(KALEIDO_APP_T *self, lv_obj_t *root)
{
    (void)self;
    lv_obj_t *body = kaleido_header(root, "Deck");
    lv_obj_remove_flag(body, LV_OBJ_FLAG_SCROLLABLE); /* the inner list scrolls */

    __cache_load();

    /* ---- status pill ---- */
    s_status = lv_obj_create(body);
    lv_obj_set_size(s_status, 300, 44);
    lv_obj_set_pos(s_status, 0, 0);
    lv_obj_set_style_radius(s_status, 12, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_status, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_status, 6, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_status, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_remove_flag(s_status, LV_OBJ_FLAG_SCROLLABLE);

    s_status_lbl = lv_label_create(s_status);
    lv_label_set_long_mode(s_status_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_status_lbl, 284);
    lv_label_set_text(s_status_lbl, "...");
    lv_obj_center(s_status_lbl);

    /* ---- brain stats ---- */
    s_stats_lbl = lv_label_create(body);
    lv_label_set_long_mode(s_stats_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_stats_lbl, 296);
    lv_label_set_text(s_stats_lbl, "");
    lv_obj_set_style_text_color(s_stats_lbl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_set_pos(s_stats_lbl, 2, 50);

    /* ---- night shift digest ---- */
    s_dg_panel = lv_obj_create(body);
    lv_obj_set_size(s_dg_panel, 300, 104);
    lv_obj_set_pos(s_dg_panel, 0, 92);
    lv_obj_set_style_bg_color(s_dg_panel, KAL_COL_FACE, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_dg_panel, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(s_dg_panel, 12, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_dg_panel, 0, LV_PART_MAIN);
    lv_obj_set_style_border_color(s_dg_panel, KAL_COL_ACCENT, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_dg_panel, 8, LV_PART_MAIN);

    lv_obj_t *dg_title = lv_label_create(s_dg_panel);
    lv_label_set_text(dg_title, LV_SYMBOL_DOWNLOAD "  NIGHT SHIFT");
    lv_obj_set_style_text_color(dg_title, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_set_pos(dg_title, 0, 0);

    s_dg_meta = lv_label_create(s_dg_panel);
    lv_label_set_text(s_dg_meta, "");
    lv_obj_set_style_text_color(s_dg_meta, KAL_COL_BG, LV_PART_MAIN);
    lv_obj_set_pos(s_dg_meta, 0, 20);

    s_dg_text = lv_label_create(s_dg_panel);
    lv_label_set_long_mode(s_dg_text, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_dg_text, 280);
    lv_label_set_text(s_dg_text, "");
    lv_obj_set_style_text_color(s_dg_text, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_set_pos(s_dg_text, 0, 40);

    /* ---- task list ---- */
    s_list = lv_obj_create(body);
    lv_obj_set_size(s_list, 300, 156);
    lv_obj_set_pos(s_list, 0, 202);
    lv_obj_set_style_bg_color(s_list, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_list, LV_OPA_50, LV_PART_MAIN);
    lv_obj_set_style_radius(s_list, 12, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_list, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_list, 8, LV_PART_MAIN);
    lv_obj_set_style_pad_row(s_list, 6, LV_PART_MAIN);
    lv_obj_set_flex_flow(s_list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(s_list, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

    /* ---- refresh button ---- */
    lv_obj_t *btn = lv_obj_create(body);
    lv_obj_set_size(btn, 150, 44);
    lv_obj_set_pos(btn, 75, 366);
    lv_obj_set_style_bg_color(btn, KAL_COL_ACCENT, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(btn, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(btn, 12, LV_PART_MAIN);
    lv_obj_set_style_border_width(btn, 0, LV_PART_MAIN);
    lv_obj_remove_flag(btn, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(btn, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(btn, __refresh_cb, LV_EVENT_CLICKED, NULL);

    s_btn_lbl = lv_label_create(btn);
    lv_label_set_text(s_btn_lbl, LV_SYMBOL_REFRESH "  REFRESH");
    lv_obj_set_style_text_color(s_btn_lbl, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_center(s_btn_lbl);

    /* First paint from the cache/demo so the screen is never empty. */
    __paint();

    /* ---- worker plumbing (created once) ---- */
    if (s_queue == NULL) {
        if (OPRT_OK != tal_queue_create_init(&s_queue, sizeof(DECK_CMD_T), 4)) {
            s_queue = NULL;
            PR_ERR("deck: queue create failed - staying in offline mode");
        }
    }
    if (s_queue != NULL && s_worker == NULL) {
        THREAD_CFG_T cfg = {
            .thrdname   = "deck",
            .priority   = THREAD_PRIO_2,
            .stackDepth = 1024 * 8, /* HTTP + cJSON both want room */
        };
        if (OPRT_OK != tal_thread_create_and_start(&s_worker, NULL, NULL, __deck_worker, NULL, &cfg)) {
            s_worker = NULL;
            PR_ERR("deck: worker start failed - staying in offline mode");
        }
    }

    lv_timer_create(__poll_cb, DECK_POLL_MS, NULL);

    /* Try the brain right away; if it isn't there, __paint() already left a
     * populated screen behind and the status pill just goes red. */
    __request_refresh();
}

/***********************************************************
********************** enter / voice ***********************
***********************************************************/
static void __enter(KALEIDO_APP_T *self)
{
    (void)self;
    __request_refresh();
}

static void __on_voice(KALEIDO_APP_T *self, const char *text)
{
    (void)self;
    if (text == NULL) {
        return;
    }

    int wants_digest = __ci_contains(text, "last night") || __ci_contains(text, "digest") ||
                       __ci_contains(text, "night shift") || __ci_contains(text, "overnight") ||
                       __ci_contains(text, "while i was asleep") || __ci_contains(text, "what did you do");
    int wants_refresh = wants_digest || __ci_contains(text, "refresh") || __ci_contains(text, "update") ||
                        __ci_contains(text, "sync") || __ci_contains(text, "check the brain") ||
                        __ci_contains(text, "status");

    if (!wants_refresh) {
        return;
    }

    if (wants_digest) {
        /* Flash the digest panel's border for ~2s so the eye lands on it. */
        lv_obj_set_style_border_width(s_dg_panel, 3, LV_PART_MAIN);
        s_flash = 2000 / DECK_POLL_MS;
    }
    __request_refresh();
}

/***********************************************************
************************* the facet ************************
***********************************************************/
KALEIDO_APP_T kaleido_app_deck = {
    .name     = "Deck",
    .track    = "LiberNovo",
    .desc     = "My other half is a little server. Deck shows what it's running and what it did all night.",
    .glyph    = LV_SYMBOL_DOWNLOAD,
    .tint     = lv_color_hex(0x6FB3F2),
    .build    = __build,
    .enter    = __enter,
    .on_voice = __on_voice,
};
