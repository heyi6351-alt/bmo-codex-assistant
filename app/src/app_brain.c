/**
 * @file app_brain.c
 * @brief Brain facet - BMO's OrangePi half, over Wi-Fi.
 *
 * BMO's body is this T5AI-Board (face, mics, speaker, touch); its brain is an
 * Orange Pi 3B on the same LAN running the "Armada" Go daemon. Brain is the
 * little window into that daemon: it polls Armada's plain-HTTP REST API and
 * shows what the brain is right now and what it got up to overnight.
 *
 *   GET /v1/status -> {"version","uptime","brain","tasks",...}
 *   GET /v1/digest -> {"ran","failed","raw","summary"}
 *
 * Threading: every byte of network I/O happens on the "brain" worker thread.
 * The worker fills a staging snapshot and raises a volatile flag; an lv_timer
 * created in build() notices the flag, copies the snapshot and repaints. No
 * locks are taken from either side (same lock-free pattern as kaleido.c). LVGL
 * objects are only ever touched from the lv_timer, i.e. the LVGL thread.
 */
#include "kaleido.h"
#include "app_face.h"

#include "tal_api.h"
#include "http_client_interface.h"
#include "cJSON.h"

#include <string.h>
#include <stdio.h>

/***********************************************************
*********************** configuration **********************
***********************************************************/
/* The Orange Pi 3B's LAN address and the Armada REST port. Plain HTTP on
 * purpose: no TLS, no cert, no cloud round-trip. */
#define BRAIN_PI_HOST      "10.68.11.43"
#define BRAIN_PI_PORT      8099
#define BRAIN_HTTP_TIMEOUT 2500  /* keep the offline path snappy on stage */
#define BRAIN_MAX_BODY     3072  /* biggest JSON body we will copy out    */
#define BRAIN_POLL_MS      200   /* UI poll timer                         */
#define BRAIN_REFRESH_MS   5000  /* worker re-polls the brain every ~5s   */

/***********************************************************
************************ data model ************************
***********************************************************/
typedef struct {
    int  online;         /* 1 = last refresh actually reached the Pi */
    char version[24];
    char uptime[28];
    char brain[28];
    int  tasks;
    char summary[256];
} BRAIN_SNAP_T;

/* s_snap is touched only by the LVGL thread; s_stage only by the worker.
 * s_ready is the one-way handoff between them (bumped last by the worker). */
static BRAIN_SNAP_T s_snap;
static BRAIN_SNAP_T s_stage;
static volatile int s_ready = 0;

static THREAD_HANDLE s_worker = NULL;

/* Armada -> BMO command channel (the brain drives its body). The worker polls
 * GET /v1/bmo/next; a queued command is staged here and applied by the LVGL
 * thread in __poll_cb - LVGL objects are never touched from the worker. */
static char s_cmd_action[16];
static char s_cmd_arg[192];
static volatile int s_cmd_ready = 0;

static lv_obj_t *s_status_lbl;
static lv_obj_t *s_version_lbl, *s_uptime_lbl, *s_brain_lbl, *s_tasks_lbl;
static lv_obj_t *s_digest_lbl;

static lv_timer_t *s_cmd_timer = NULL;

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
    /* "Connection: close" — one fresh socket per request. The daemon restarts
     * during deploys, and a reused keep-alive connection to a dead listener
     * fails with SEND_FAULT instead of reconnecting. */
    http_client_header_t hdrs[] = {
        { .key = "Connection", .value = "close" },
    };
    http_client_request_t req = {
        .host          = BRAIN_PI_HOST,
        .port          = BRAIN_PI_PORT,
        .path          = path,
        .method        = "GET",
        .headers       = hdrs,
        .headers_count = 1,
        .body          = NULL,
        .body_length   = 0,
        .timeout_ms    = BRAIN_HTTP_TIMEOUT,
        /* plain HTTP: cacert stays NULL and tls_no_verify stays false */
    };

    http_client_response_t resp = {0};
    char                  *out  = NULL;

    http_client_status_t st = http_client_request(&req, &resp);
    if (st == HTTP_CLIENT_SUCCESS && resp.status_code == 200 && resp.body && resp.body_length) {
        size_t nlen = resp.body_length;
        if (nlen > BRAIN_MAX_BODY) {
            nlen = BRAIN_MAX_BODY;
        }
        out = tal_malloc(nlen + 1);
        if (out) {
            memcpy(out, resp.body, nlen);
            out[nlen] = 0;
        }
    } else if (st == HTTP_CLIENT_SUCCESS && resp.status_code == 204) {
        /* 204 = a valid empty answer (e.g. no queued command) - stay quiet. */
    } else {
        PR_WARN("brain: GET %s failed (st=%d http=%u)", path, st, resp.status_code);
    }

    http_client_free(&resp);
    return out;
}

static void __parse_status(const char *json, BRAIN_SNAP_T *s)
{
    cJSON *r = cJSON_Parse(json);
    if (r == NULL) {
        return;
    }
    __cp(s->version, sizeof(s->version), __jstr(r, "version"));
    __cp(s->brain,   sizeof(s->brain),   __jstr(r, "brain"));
    __fmt_uptime(r, s->uptime, sizeof(s->uptime));
    s->tasks = __jint(r, "tasks", s->tasks);
    cJSON_Delete(r);
}

static void __parse_digest(const char *json, BRAIN_SNAP_T *s)
{
    cJSON *r = cJSON_Parse(json);
    if (r == NULL) {
        return;
    }
    const char *sum = __jstr(r, "summary");
    if (sum == NULL || sum[0] == 0) {
        sum = __jstr(r, "raw"); /* fall back to the unsummarised text */
    }
    if (sum && sum[0]) {
        __cp(s->summary, sizeof(s->summary), sum);
    }
    cJSON_Delete(r);
}

/* One full refresh cycle. Worker thread only. */
static void __do_poll(void)
{
    /* Start from the last known good picture so a partial failure degrades into
     * "some fields are stale" instead of "the screen went blank". */
    memcpy(&s_stage, &s_snap, sizeof(s_stage));

    char *js = __http_get("/v1/status");
    if (js == NULL) {
        s_stage.online = 0;
        s_ready        = 1;
        return;
    }
    s_stage.online = 1;
    __parse_status(js, &s_stage);
    tal_free(js);

    js = __http_get("/v1/digest");
    if (js) {
        __parse_digest(js, &s_stage);
        tal_free(js);
    }

    /* The other direction: does the brain want its body to do something?
     * Pop one queued command per cycle and hand it to the LVGL thread. */
    js = __http_get("/v1/bmo/next");
    if (js) {
        cJSON *r = cJSON_Parse(js);
        if (r) {
            const char *act = __jstr(r, "action");
            const char *arg = __jstr(r, "arg");
            if (act && act[0] && !s_cmd_ready) {
                __cp(s_cmd_action, sizeof(s_cmd_action), act);
                __cp(s_cmd_arg, sizeof(s_cmd_arg), arg ? arg : "");
                s_cmd_ready = 1;
            }
            cJSON_Delete(r);
        }
        tal_free(js);
    }

    s_ready = 1;
}

static void __brain_worker(void *arg)
{
    (void)arg;
    for (;;) {
        __do_poll();
        tal_system_sleep(BRAIN_REFRESH_MS);
    }
}

/***********************************************************
************************* painting *************************
***********************************************************/
static void __paint(void)
{
    if (s_snap.online) {
        lv_label_set_text_fmt(s_status_lbl, LV_SYMBOL_OK "  Brain online  %s", BRAIN_PI_HOST);
        lv_obj_set_style_text_color(s_status_lbl, lv_color_hex(0x7BD389), LV_PART_MAIN);
    } else {
        lv_label_set_text(s_status_lbl, LV_SYMBOL_WARNING "  Reaching the brain...");
        lv_obj_set_style_text_color(s_status_lbl, KAL_COL_ACCENT, LV_PART_MAIN);
    }

    lv_label_set_text_fmt(s_version_lbl, "Version:  %s",
                          s_snap.version[0] ? s_snap.version : "armada ?");
    lv_label_set_text_fmt(s_uptime_lbl, "Uptime:   %s",
                          s_snap.uptime[0] ? s_snap.uptime : "?");
    lv_label_set_text_fmt(s_brain_lbl, "Brain:    %s",
                          s_snap.brain[0] ? s_snap.brain : "orangepi-3b");
    lv_label_set_text_fmt(s_tasks_lbl, "Tasks:    %d", s_snap.tasks);

    lv_label_set_text(s_digest_lbl, s_snap.summary[0]
                                        ? s_snap.summary
                                        : "No digest yet. Waiting for the brain to report in...");
}

/***********************************************************
*********************** LVGL callbacks *********************
***********************************************************/
/* Command timer: lives for the whole session (created by brain_facet_start at
 * boot), so the brain can drive the body even while nobody is watching the
 * Brain facet. Runs on the LVGL thread - safe to touch face and toasts. */
static void __cmd_cb(lv_timer_t *t)
{
    (void)t;
    if (s_cmd_ready) {
        char action[16], arg[192];
        s_cmd_ready = 0;
        __cp(action, sizeof(action), s_cmd_action);
        __cp(arg, sizeof(arg), s_cmd_arg);
        if (0 == strcmp(action, "face")) {
            app_face_set_by_name(arg);
        } else if (0 == strcmp(action, "say")) {
            kaleido_toast(arg);
            app_face_set_by_name("SPEAKING");
        } else if (0 == strcmp(action, "open")) {
            /* The daemon picks what the body shows: "trader", "brain",
             * "apps", "home". Goes through the shell's lock-free request
             * flags - we are inside an lv_timer, the display lock is held. */
            if (0 == strcmp(arg, "trader")) {
                kaleido_request_open(&kaleido_app_trader);
            } else if (0 == strcmp(arg, "brain")) {
                kaleido_request_open(&kaleido_app_brain);
            } else if (0 == strcmp(arg, "apps")) {
                kaleido_request_apps();
            } else if (0 == strcmp(arg, "home")) {
                kaleido_request_home();
            }
        }
    }
}

static void __poll_cb(lv_timer_t *t)
{
    (void)t;
    if (s_ready) {
        s_ready = 0;
        memcpy(&s_snap, &s_stage, sizeof(s_snap));
        __paint();
    }
}

/***********************************************************
************************** start ***************************
***********************************************************/
/* Start the brain link (worker + command timer). Called once at boot from
 * app_chat_bot.c, and again harmlessly when the Brain facet is opened. */
void brain_facet_start(void)
{
    if (s_worker == NULL) {
        THREAD_CFG_T cfg = {
            .thrdname   = "brain",
            .priority   = THREAD_PRIO_2,
            .stackDepth = 1024 * 8, /* HTTP + cJSON both want room */
        };
        if (OPRT_OK != tal_thread_create_and_start(&s_worker, NULL, NULL, __brain_worker, NULL, &cfg)) {
            s_worker = NULL;
            PR_ERR("brain: worker start failed - staying offline");
        }
    }
    if (s_cmd_timer == NULL) {
        s_cmd_timer = lv_timer_create(__cmd_cb, BRAIN_POLL_MS, NULL);
    }
}

/***********************************************************
************************** build ***************************
***********************************************************/
static void __build(KALEIDO_APP_T *self, lv_obj_t *root)
{
    (void)self;
    lv_obj_t *body = kaleido_header(root, "Brain");

    /* ---- status line ---- */
    s_status_lbl = lv_label_create(body);
    lv_label_set_long_mode(s_status_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_status_lbl, KAL_W - 40);
    lv_label_set_text(s_status_lbl, LV_SYMBOL_WARNING "  Reaching the brain...");
    lv_obj_set_style_text_color(s_status_lbl, KAL_COL_ACCENT, LV_PART_MAIN);
    lv_obj_align(s_status_lbl, LV_ALIGN_TOP_LEFT, 0, 0);

    /* ---- brain stats ---- */
    s_version_lbl = lv_label_create(body);
    lv_label_set_text(s_version_lbl, "Version:  ...");
    lv_obj_set_style_text_color(s_version_lbl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(s_version_lbl, LV_ALIGN_TOP_LEFT, 0, 34);

    s_uptime_lbl = lv_label_create(body);
    lv_label_set_text(s_uptime_lbl, "Uptime:   ...");
    lv_obj_set_style_text_color(s_uptime_lbl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(s_uptime_lbl, LV_ALIGN_TOP_LEFT, 0, 58);

    s_brain_lbl = lv_label_create(body);
    lv_label_set_text(s_brain_lbl, "Brain:    ...");
    lv_obj_set_style_text_color(s_brain_lbl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(s_brain_lbl, LV_ALIGN_TOP_LEFT, 0, 82);

    s_tasks_lbl = lv_label_create(body);
    lv_label_set_text(s_tasks_lbl, "Tasks:    ...");
    lv_obj_set_style_text_color(s_tasks_lbl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(s_tasks_lbl, LV_ALIGN_TOP_LEFT, 0, 106);

    /* ---- night-shift digest panel ---- */
    lv_obj_t *panel = lv_obj_create(body);
    lv_obj_set_size(panel, KAL_W - 20, 132);
    lv_obj_set_pos(panel, 0, 140);
    lv_obj_set_style_bg_color(panel, KAL_COL_FACE, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(panel, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(panel, 12, LV_PART_MAIN);
    lv_obj_set_style_border_width(panel, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(panel, 8, LV_PART_MAIN);
    lv_obj_remove_flag(panel, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *dg_title = lv_label_create(panel);
    lv_label_set_text(dg_title, LV_SYMBOL_LOOP "  LATEST DIGEST");
    lv_obj_set_style_text_color(dg_title, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_align(dg_title, LV_ALIGN_TOP_LEFT, 0, 0);

    s_digest_lbl = lv_label_create(panel);
    lv_label_set_long_mode(s_digest_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_digest_lbl, KAL_W - 52);
    lv_label_set_text(s_digest_lbl, "Waiting for the brain to report in...");
    lv_obj_set_style_text_color(s_digest_lbl, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_align(s_digest_lbl, LV_ALIGN_TOP_LEFT, 0, 24);

    /* First paint from whatever we have (empty on the very first open). */
    __paint();

    /* ---- worker + command plumbing (idempotent; usually already running) ---- */
    brain_facet_start();

    lv_timer_create(__poll_cb, BRAIN_POLL_MS, NULL);
}

/***********************************************************
************************* the facet ************************
***********************************************************/
KALEIDO_APP_T kaleido_app_brain = {
    .name  = "Brain",
    .track = "LiberNovo",
    .desc  = "My other half is a little OrangePi server. Brain shows what Armada is doing right now.",
    .glyph = LV_SYMBOL_LOOP,
    .tint  = 0x6FB3F2,
    .build = __build,
};
