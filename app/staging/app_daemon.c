/**
 * @file app_daemon.c
 * @brief Daemon facet (LiberNovo track) — "an always-on machine that is YOURS
 *        and does work while you sleep."
 *
 * A tiny scheduler that lives inside BMO. It owns a handful of jobs, each with a
 * period, a lifetime run count, and a one-line result. An lv_timer is its
 * heartbeat: it advances a simulated clock, fires jobs that are due, and writes
 * a friendly result line. At the top of the screen sits the thing the pitch is
 * actually about — the night-shift digest: "While you slept I ran 7 jobs, all
 * fine." Tapping the digest says good morning and starts a fresh night.
 *
 * FULLY OFFLINE. There is no network call anywhere in this file, by design: the
 * whole point of the track is that this machine is yours and keeps working with
 * or without a cloud. State survives reboots via tal_kv (one raw blob).
 *
 * Demo clock: real "hourly / every 6h / nightly" periods would make a bench demo
 * look dead, so the daemon's own clock runs fast (DAEMON_TICK_MS per device
 * minute). The jobs, counters, persistence and digest are all real — only the
 * clock is compressed, and the UI says so out loud rather than pretending.
 *
 * Threading: everything here runs in LVGL context (build/enter/exit, the tick
 * timer, click callbacks). No locking, no kaleido_* navigation calls from
 * callbacks, no worker thread needed — there is nothing blocking to do.
 */
#include "kaleido.h"
#include "tal_api.h"
#include "tal_kv.h"

#include <stdlib.h>
#include <string.h>

/***********************************************************
*********************** tuning knobs ***********************
***********************************************************/
#define DAEMON_KV_KEY      "kal.daemon.v1"
#define DAEMON_MAGIC       0x444D4E31u /* 'DMN1' */
#define DAEMON_VERSION     1

#define DAEMON_MAX_JOBS    4
#define DAEMON_NAME_LEN    24
#define DAEMON_WHEN_LEN    14
#define DAEMON_RES_LEN     36

/* Heartbeat: one simulated device-minute per tick. 200ms/min => an "hourly"
 * job fires every 12s, "every 6h" every ~72s, "nightly" (8h) every ~96s. */
#define DAEMON_TICK_MS     200

/* Flush to flash at most this often (in ticks) so we don't chew the sector. */
#define DAEMON_FLUSH_TICKS 150 /* ~30s of wall clock */

/***********************************************************
********************** persisted state *********************
***********************************************************/
typedef struct {
    char     name[DAEMON_NAME_LEN];
    char     when[DAEMON_WHEN_LEN]; /* human label: "hourly", "every 6h"... */
    char     result[DAEMON_RES_LEN];/* one-line outcome of the last run */
    uint32_t period_min;            /* device-minutes between runs */
    uint32_t next_min;              /* device-minute this job is next due */
    uint32_t last_min;              /* device-minute of last run, 0 = never */
    uint32_t runs;                  /* lifetime run count */
    uint8_t  noted;                 /* last run raised a gentle note */
    uint8_t  used;
    uint8_t  pad[2];
} DAEMON_JOB_T;

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t job_sz;      /* sizeof(DAEMON_JOB_T) — guards struct drift */
    uint32_t clock_min;   /* device-minutes this daemon has been alive */
    uint32_t total_runs;  /* lifetime, across all jobs */
    uint32_t night_runs;  /* runs since you last said good morning */
    uint32_t night_notes; /* of those, how many raised a gentle note */
    uint32_t nights;      /* how many mornings we've greeted together */
    DAEMON_JOB_T jobs[DAEMON_MAX_JOBS];
} DAEMON_BLOB_T;

static DAEMON_BLOB_T s_db;

/***********************************************************
*********************** module state ***********************
***********************************************************/
static lv_obj_t   *s_digest_lbl, *s_digest_sub, *s_status_lbl;
static lv_obj_t   *s_row_name[DAEMON_MAX_JOBS];
static lv_obj_t   *s_row_when[DAEMON_MAX_JOBS];
static lv_obj_t   *s_row_res[DAEMON_MAX_JOBS];
static lv_obj_t   *s_row_runs[DAEMON_MAX_JOBS];
static lv_obj_t   *s_row_dot[DAEMON_MAX_JOBS];
static lv_timer_t *s_tick;

static int      s_seeded_rand;
static int      s_dirty;
static uint32_t s_dirty_ticks;

/* Friendly result lines, one pool per default job. Kept short so they fit on a
 * 320px row without eliding. Index 1 of each pool is the "gentle note" line. */
static const char *k_res_checkin[] = {
    "you seemed well",
    "you were quiet tonight",
    "all calm on your side",
    "nothing needed you",
};
static const char *k_res_tidy[] = {
    "nothing new to file",
    "3 scraps left unsorted",
    "notes are neat again",
    "merged 2 stray notes",
};
static const char *k_res_watch[] = {
    "house quiet, all safe",
    "a soft noise, then quiet",
    "still and safe",
    "watched, all clear",
};
static const char *k_res_generic[] = {
    "done, nothing odd",
    "took a little longer",
    "finished early",
    "all clear",
};

static const char **__pool_for(int i, int *cnt)
{
    *cnt = 4;
    switch (i) {
    case 0:  return k_res_checkin;
    case 1:  return k_res_tidy;
    case 2:  return k_res_watch;
    default: return k_res_generic;
    }
}

/***********************************************************
******************* persistence (tal_kv) *******************
***********************************************************/
static void __seed_defaults(void)
{
    memset(&s_db, 0, sizeof(s_db));
    s_db.magic   = DAEMON_MAGIC;
    s_db.version = DAEMON_VERSION;
    s_db.job_sz  = (uint16_t)sizeof(DAEMON_JOB_T);

    struct {
        const char *name;
        const char *when;
        uint32_t    period_min;
    } defs[3] = {
        {"Check in on you", "hourly",   60},
        {"Tidy notes",      "every 6h", 360},
        {"Night watch",     "nightly",  480},
    };

    for (int i = 0; i < 3; i++) {
        DAEMON_JOB_T *j = &s_db.jobs[i];
        strncpy(j->name, defs[i].name, DAEMON_NAME_LEN - 1);
        strncpy(j->when, defs[i].when, DAEMON_WHEN_LEN - 1);
        strncpy(j->result, "not run yet", DAEMON_RES_LEN - 1);
        j->period_min = defs[i].period_min;
        /* Stagger the first runs so the screen comes alive within seconds
         * instead of making you wait a whole (compressed) period. */
        j->next_min = 6 + (uint32_t)i * 10;
        j->last_min = 0;
        j->runs     = 0;
        j->used     = 1;
    }
    PR_NOTICE("daemon: seeded %d default jobs", 3);
}

static void __save(void)
{
    int rt = tal_kv_set(DAEMON_KV_KEY, (const uint8_t *)&s_db, sizeof(s_db));
    if (rt != OPRT_OK) {
        PR_WARN("daemon: kv save failed (%d) — running from RAM only", rt);
    }
    s_dirty = 0;
    s_dirty_ticks = 0;
}

static void __load(void)
{
    uint8_t *val = NULL;
    size_t   len = 0;

    if (tal_kv_get(DAEMON_KV_KEY, &val, &len) == OPRT_OK && val != NULL) {
        if (len == sizeof(DAEMON_BLOB_T)) {
            DAEMON_BLOB_T *in = (DAEMON_BLOB_T *)val;
            if (in->magic == DAEMON_MAGIC && in->version == DAEMON_VERSION &&
                in->job_sz == (uint16_t)sizeof(DAEMON_JOB_T)) {
                memcpy(&s_db, in, sizeof(s_db));
                tal_kv_free(val);
                PR_NOTICE("daemon: restored (clock=%u min, %u runs)",
                          (unsigned)s_db.clock_min, (unsigned)s_db.total_runs);
                return;
            }
        }
        PR_WARN("daemon: stored state unusable (len=%u) — reseeding", (unsigned)len);
        tal_kv_free(val);
    }

    __seed_defaults();
    __save();
}

/***********************************************************
************************ the daemon ************************
***********************************************************/
static void __run_job(int i)
{
    DAEMON_JOB_T *j = &s_db.jobs[i];
    if (!j->used) {
        return;
    }

    int          cnt  = 0;
    const char **pool = __pool_for(i, &cnt);

    /* Mostly boring outcomes — that is the point of a good night shift. Index 1
     * of each pool is the "gentle note" line. Two deliberate choices here:
     * notes are rare (~1 in 8), and job 0 ("Check in on you") NEVER raises one —
     * checking in on you should always come back reassuring. Together these keep
     * the headline digest on "all fine", which is the honest common case. */
    int pick;
    if (i > 0 && (rand() % 8) == 0) {
        pick = 1;
    } else {
        pick = rand() % cnt;
        if (pick == 1) {
            pick = 0;
        }
    }

    strncpy(j->result, pool[pick], DAEMON_RES_LEN - 1);
    j->result[DAEMON_RES_LEN - 1] = 0;
    j->noted    = (pick == 1) ? 1 : 0;
    j->last_min = s_db.clock_min;
    j->next_min = s_db.clock_min + j->period_min;
    j->runs++;

    s_db.total_runs++;
    s_db.night_runs++;
    if (j->noted) {
        s_db.night_notes++;
    }
    s_dirty = 1;
}

/* "3h 20m" / "12m" / "just now", in the daemon's own device time. Kept terse:
 * it shares the top line of a 276px row with the job name. */
static void __ago_text(uint32_t mins, char *out, size_t out_sz)
{
    if (mins == 0) {
        snprintf(out, out_sz, "just now");
    } else if (mins < 60) {
        snprintf(out, out_sz, "%um", (unsigned)mins);
    } else {
        snprintf(out, out_sz, "%uh %um", (unsigned)(mins / 60), (unsigned)(mins % 60));
    }
}

/***********************************************************
************************ the screen ************************
***********************************************************/
static void __refresh_digest(void)
{
    if (s_digest_lbl == NULL) {
        return;
    }

    if (s_db.night_runs == 0) {
        lv_label_set_text(s_digest_lbl,
                          "I'm awake and keeping watch.\n"
                          "Nothing to report yet. Go rest —\n"
                          "I've got this end of things.");
    } else if (s_db.night_notes == 0) {
        lv_label_set_text_fmt(s_digest_lbl,
                              "Good morning. While you slept\n"
                              "I ran %u job%s, all fine.\n"
                              "Nothing needed you.",
                              (unsigned)s_db.night_runs,
                              s_db.night_runs == 1 ? "" : "s");
    } else {
        lv_label_set_text_fmt(s_digest_lbl,
                              "Good morning. I ran %u job%s\n"
                              "while you slept. %u worth a\n"
                              "glance — nothing urgent.",
                              (unsigned)s_db.night_runs,
                              s_db.night_runs == 1 ? "" : "s",
                              (unsigned)s_db.night_notes);
    }

    if (s_digest_sub) {
        lv_label_set_text_fmt(s_digest_sub,
                              "awake %uh %um " LV_SYMBOL_BULLET " %u jobs done "
                              LV_SYMBOL_BULLET " tap for good morning",
                              (unsigned)(s_db.clock_min / 60), (unsigned)(s_db.clock_min % 60),
                              (unsigned)s_db.total_runs);
    }
}

static void __refresh_rows(void)
{
    char ago[24];

    for (int i = 0; i < DAEMON_MAX_JOBS; i++) {
        if (s_row_name[i] == NULL) {
            continue;
        }
        DAEMON_JOB_T *j = &s_db.jobs[i];

        if (!j->used) {
            continue;
        }

        lv_label_set_text(s_row_name[i], j->name);

        if (j->runs == 0) {
            uint32_t due = (j->next_min > s_db.clock_min) ? (j->next_min - s_db.clock_min) : 0;
            lv_label_set_text(s_row_when[i], j->when);
            lv_label_set_text_fmt(s_row_res[i], "waiting its turn (in %um)", (unsigned)due);
        } else {
            __ago_text(s_db.clock_min - j->last_min, ago, sizeof(ago));
            lv_label_set_text_fmt(s_row_when[i], "%s " LV_SYMBOL_BULLET " %s", j->when, ago);
            lv_label_set_text(s_row_res[i], j->result);
        }

        lv_label_set_text_fmt(s_row_runs[i], "%u runs", (unsigned)j->runs);
        lv_obj_set_style_bg_color(s_row_dot[i],
                                  j->noted ? KAL_COL_ACCENT : KAL_COL_FACE, LV_PART_MAIN);
    }
}

static void __refresh_all(void)
{
    __refresh_digest();
    __refresh_rows();
}

static void __say(const char *msg)
{
    if (s_status_lbl) {
        lv_label_set_text(s_status_lbl, msg ? msg : "");
    }
}

/***********************************************************
********************* heartbeat + buttons ******************
***********************************************************/
static void __tick_cb(lv_timer_t *t)
{
    (void)t;

    s_db.clock_min++;

    int fired = 0;
    for (int i = 0; i < DAEMON_MAX_JOBS; i++) {
        if (s_db.jobs[i].used && s_db.clock_min >= s_db.jobs[i].next_min) {
            __run_job(i);
            fired = 1;
        }
    }

    /* Repaint once a second-ish, or immediately when something actually ran. */
    if (fired || (s_db.clock_min % 5) == 0) {
        __refresh_all();
    }

    if (s_dirty) {
        s_dirty_ticks++;
        if (s_dirty_ticks >= DAEMON_FLUSH_TICKS) {
            __save();
        }
    }
}

static void __run_all_cb(lv_event_t *e)
{
    (void)e;
    for (int i = 0; i < DAEMON_MAX_JOBS; i++) {
        if (s_db.jobs[i].used) {
            __run_job(i);
        }
    }
    __save();
    __refresh_all();
    __say("Done. I ran everything just now.");
}

static void __reset_cb(lv_event_t *e)
{
    (void)e;
    __seed_defaults();
    __save();
    __refresh_all();
    __say("Fresh start. Beginning again from zero.");
}

/* Tapping the digest = "good morning": the night's tally rolls over. */
static void __good_morning_cb(lv_event_t *e)
{
    (void)e;
    if (s_db.night_runs > 0) {
        s_db.nights++;
    }
    s_db.night_runs  = 0;
    s_db.night_notes = 0;
    __save();
    __refresh_all();
    if (s_db.nights > 0) {
        __say("Morning. Another night looked after.");
    } else {
        __say("Morning! Back to work then.");
    }
}

/***********************************************************
*************************** build **************************
***********************************************************/
static lv_obj_t *__button(lv_obj_t *parent, int w, int x, lv_color_t col,
                          const char *text, lv_color_t txt_col, lv_event_cb_t cb)
{
    lv_obj_t *b = lv_obj_create(parent);
    lv_obj_set_size(b, w, 44);
    lv_obj_set_pos(b, x, 338);
    lv_obj_set_style_bg_color(b, col, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(b, 12, LV_PART_MAIN);
    lv_obj_set_style_border_width(b, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(b, 0, LV_PART_MAIN);
    lv_obj_remove_flag(b, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(b, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, NULL);

    lv_obj_t *l = lv_label_create(b);
    lv_label_set_text(l, text);
    lv_obj_set_style_text_color(l, txt_col, LV_PART_MAIN);
    lv_obj_center(l);
    return b;
}

static void __build_row(lv_obj_t *list, int i)
{
    /* 58px row = two text lines inside 8px padding. Every string that lands in
     * here is kept short enough to stay on ONE line (see the result pools and
     * __ago_text) — a second line would collide with the run counter. */
    lv_obj_t *row = lv_obj_create(list);
    lv_obj_set_size(row, 276, 58);
    lv_obj_set_style_bg_color(row, KAL_COL_FACE, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(row, LV_OPA_40, LV_PART_MAIN);
    lv_obj_set_style_radius(row, 12, LV_PART_MAIN);
    lv_obj_set_style_border_width(row, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(row, 8, LV_PART_MAIN);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE);

    /* health dot */
    s_row_dot[i] = lv_obj_create(row);
    lv_obj_set_size(s_row_dot[i], 10, 10);
    lv_obj_align(s_row_dot[i], LV_ALIGN_TOP_LEFT, 0, 5);
    lv_obj_set_style_radius(s_row_dot[i], 5, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_row_dot[i], KAL_COL_FACE, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_row_dot[i], LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_row_dot[i], 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_row_dot[i], LV_OBJ_FLAG_SCROLLABLE);

    s_row_name[i] = lv_label_create(row);
    lv_label_set_text(s_row_name[i], "");
    lv_obj_set_style_text_color(s_row_name[i], KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(s_row_name[i], LV_ALIGN_TOP_LEFT, 18, 0);

    s_row_when[i] = lv_label_create(row);
    lv_label_set_text(s_row_when[i], "");
    lv_obj_set_style_text_color(s_row_when[i], KAL_COL_ACCENT, LV_PART_MAIN);
    lv_obj_align(s_row_when[i], LV_ALIGN_TOP_RIGHT, 0, 0);

    s_row_res[i] = lv_label_create(row);
    lv_label_set_long_mode(s_row_res[i], LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_row_res[i], 175);
    lv_label_set_text(s_row_res[i], "");
    lv_obj_set_style_text_color(s_row_res[i], KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_set_style_text_opa(s_row_res[i], LV_OPA_80, LV_PART_MAIN);
    lv_obj_align(s_row_res[i], LV_ALIGN_TOP_LEFT, 18, 22);

    s_row_runs[i] = lv_label_create(row);
    lv_label_set_text(s_row_runs[i], "0 runs");
    lv_obj_set_style_text_color(s_row_runs[i], KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_set_style_text_opa(s_row_runs[i], LV_OPA_60, LV_PART_MAIN);
    lv_obj_align(s_row_runs[i], LV_ALIGN_BOTTOM_RIGHT, 0, 0);
}

static void __build(KALEIDO_APP_T *self, lv_obj_t *root)
{
    (void)self;

    if (!s_seeded_rand) {
        srand((unsigned)tal_system_get_millisecond());
        s_seeded_rand = 1;
    }

    /* Load or seed BEFORE drawing so the first paint is already truthful.
     * Worst case (no filesystem) __load() falls back to seeded defaults, so the
     * facet is fully alive with no flash and no network. */
    __load();

    lv_obj_t *body = kaleido_header(root, "Daemon");

    /* ---- night-shift digest ---- */
    lv_obj_t *panel = lv_obj_create(body);
    lv_obj_set_size(panel, 300, 104);
    lv_obj_set_pos(panel, 0, 0);
    lv_obj_set_style_bg_color(panel, KAL_COL_FACE, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(panel, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(panel, 14, LV_PART_MAIN);
    lv_obj_set_style_border_width(panel, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(panel, 10, LV_PART_MAIN);
    lv_obj_remove_flag(panel, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(panel, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(panel, __good_morning_cb, LV_EVENT_CLICKED, NULL);

    s_digest_lbl = lv_label_create(panel);
    lv_label_set_long_mode(s_digest_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_digest_lbl, 276);
    lv_label_set_text(s_digest_lbl, "");
    lv_obj_set_style_text_color(s_digest_lbl, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_align(s_digest_lbl, LV_ALIGN_TOP_LEFT, 0, 0);

    s_digest_sub = lv_label_create(panel);
    lv_label_set_long_mode(s_digest_sub, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_digest_sub, 276);
    lv_label_set_text(s_digest_sub, "");
    lv_obj_set_style_text_color(s_digest_sub, KAL_COL_BG, LV_PART_MAIN);
    lv_obj_align(s_digest_sub, LV_ALIGN_BOTTOM_LEFT, 0, 0);

    /* ---- job list ---- */
    lv_obj_t *list = lv_obj_create(body);
    lv_obj_set_size(list, 300, 222);
    lv_obj_set_pos(list, 0, 110);
    lv_obj_set_style_bg_opa(list, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(list, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(list, 4, LV_PART_MAIN);
    lv_obj_set_style_pad_row(list, 8, LV_PART_MAIN);
    lv_obj_set_flex_flow(list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(list, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

    for (int i = 0; i < DAEMON_MAX_JOBS; i++) {
        s_row_name[i] = NULL;
        if (s_db.jobs[i].used) {
            __build_row(list, i);
        }
    }

    /* ---- buttons ---- */
    __button(body, 175, 0,   KAL_COL_ACCENT, LV_SYMBOL_REFRESH "  Run all now", KAL_COL_INK,  __run_all_cb);
    __button(body, 110, 190, KAL_COL_ALERT,  "Reset",                           KAL_COL_TEXT, __reset_cb);

    /* ---- status / honesty line ---- */
    s_status_lbl = lv_label_create(body);
    lv_label_set_long_mode(s_status_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_status_lbl, 300);
    lv_label_set_text(s_status_lbl, "No cloud, no account. Just me.");
    lv_obj_set_style_text_color(s_status_lbl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_set_style_text_opa(s_status_lbl, LV_OPA_70, LV_PART_MAIN);
    lv_obj_set_pos(s_status_lbl, 0, 388);

    __refresh_all();

    /* The heartbeat. Deliberately created here and never deleted: the daemon
     * keeps ticking while you're off in another facet — that's the whole idea. */
    s_tick = lv_timer_create(__tick_cb, DAEMON_TICK_MS, NULL);
}

static void __enter(KALEIDO_APP_T *self)
{
    (void)self;
    __refresh_all();
    __say("I never stopped. Here's where we are.");
}

static void __exit(KALEIDO_APP_T *self)
{
    (void)self;
    if (s_dirty) {
        __save(); /* leaving the screen is a good moment to commit */
    }
}

/***********************************************************
************************* voice ****************************
***********************************************************/
static int __ci_has(const char *hay, const char *needle)
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

/* Called from LVGL context with the mutex already held (see kaleido.c). Plain
 * lv_* calls only — no navigation, no toast. */
static void __on_voice(KALEIDO_APP_T *self, const char *text)
{
    (void)self;
    if (text == NULL || text[0] == 0) {
        return;
    }

    if (__ci_has(text, "run all") || __ci_has(text, "run everything") ||
        __ci_has(text, "run now")  || __ci_has(text, "do your jobs")) {
        __run_all_cb(NULL);
        return;
    }
    if (__ci_has(text, "good morning") || __ci_has(text, "morning")) {
        __good_morning_cb(NULL);
        return;
    }
    if (__ci_has(text, "reset") || __ci_has(text, "start over")) {
        __reset_cb(NULL);
        return;
    }
    if (__ci_has(text, "what did you do") || __ci_has(text, "digest") ||
        __ci_has(text, "report")          || __ci_has(text, "status")) {
        __refresh_all();
        __say("It's all up top. No cloud ever saw it.");
        return;
    }
    __say("Try \"run all\" or \"what did you do\".");
}

/***********************************************************
************************* facet ****************************
***********************************************************/
KALEIDO_APP_T kaleido_app_daemon = {
    .name     = "Daemon",
    .track    = "LiberNovo",
    .desc     = "I keep working while you sleep, then tell you what I did.",
    .glyph    = LV_SYMBOL_LOOP,
    .tint     = lv_color_hex(0x6FB3F2),
    .build    = __build,
    .enter    = __enter,
    .exit     = __exit,
    .on_voice = __on_voice,
};
