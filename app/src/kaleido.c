/**
 * @file kaleido.c
 * @brief Kaleidoscope shell — see kaleido.h. LVGL 9, 320x480 touch.
 *
 * Screens: intro -> home (BMO face) -> apps (tile grid) -> facet screens, plus
 * the existing ai_ui chat screen (captured at start) reachable as "Chat".
 *
 * Locking model: kaleido_start() and the *public* nav functions run from
 * non-LVGL threads and take the LVGL mutex. Everything reached from a tile click
 * or an lv_timer already runs inside LVGL's task (mutex held), so those paths use
 * the __-prefixed lock-free helpers. Never call a locking function from a click.
 */
#include "kaleido.h"
#include "app_face.h"
#include "app_guardian.h"

#include "tal_api.h"
#include "lv_vendor.h"   /* LVGL task lock for this platform (lv_vendor_disp_lock/unlock) */
#include <string.h>

/***********************************************************
*********************** module state ***********************
***********************************************************/
static KALEIDO_APP_T *s_apps[KAL_MAX_APPS];
static int            s_app_cnt = 0;
static KALEIDO_APP_T *s_active = NULL;

static lv_obj_t *s_intro_scr = NULL;
static lv_obj_t *s_home_scr  = NULL;
static lv_obj_t *s_apps_scr  = NULL;
static lv_obj_t *s_about_scr = NULL; /* "Who am I" — BMO's feature list */
static lv_obj_t *s_chat_scr  = NULL; /* the ai_ui chat screen (default screen) */
static lv_obj_t *s_face_area = NULL; /* container hosting the BMO face on home */

/* "Chat" is a pseudo-facet whose screen is the pre-existing ai_ui screen. */
static KALEIDO_APP_T s_chat_facet = {
    .name = "Chat", .track = "Tuya \xC2\xB7 Companion", .desc = "Talk with me about anything.",
    .glyph = LV_SYMBOL_CALL, .tint = 0xF6C453,
};

static inline void kal_lock(void)   { lv_vendor_disp_lock(); }
static inline void kal_unlock(void) { lv_vendor_disp_unlock(); }

/* Cross-thread UI requests. The guardian and AI-event callbacks run in non-LVGL
 * tasks; and some callers (a button click) are already inside LVGL with the mutex
 * held — locking again would deadlock a non-recursive mutex. So those paths just
 * set these flags, and __ui_poll_cb (an lv_timer, i.e. LVGL context) applies them
 * safely without any locking. */
static void __kaleido_open(KALEIDO_APP_T *app); /* defined below; used by __ui_poll_cb */

static volatile int s_face_req = -1; /* an APP_FACE_STATE_E, or -1 = no change */
static volatile int s_home_req = 0;  /* 1 = jump to the home screen */
static volatile int s_apps_req = 0;  /* 1 = jump to the apps grid  */
static volatile KALEIDO_APP_T *s_open_req = NULL; /* facet to open, NULL = none */

static void __ui_poll_cb(lv_timer_t *t)
{
    (void)t;
    if (s_home_req) {
        s_home_req = 0;
        lv_screen_load(s_home_scr);
        s_active = NULL;
    }
    if (s_apps_req) {
        s_apps_req = 0;
        lv_screen_load(s_apps_scr);
        s_active = NULL;
    }
    KALEIDO_APP_T *open = (KALEIDO_APP_T *)s_open_req;
    if (open) {
        s_open_req = NULL;
        __kaleido_open(open);
    }
    int fr = s_face_req;
    if (fr >= 0) {
        s_face_req = -1;
        app_face_set_state((APP_FACE_STATE_E)fr);
    }
}

/***********************************************************
******************** lock-free helpers *********************
***********************************************************/
static lv_obj_t *__bare_screen(void)
{
    lv_obj_t *scr = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(scr, KAL_COL_BG, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
    return scr;
}

static void __to_apps_cb(lv_event_t *e)  { (void)e; lv_screen_load(s_apps_scr); s_active = NULL; }
static void __to_home_cb(lv_event_t *e)  { (void)e; lv_screen_load(s_home_scr); s_active = NULL; }

static void __kaleido_open(KALEIDO_APP_T *app)
{
    if (app == NULL) {
        return;
    }
    if (app->screen == NULL) {
        app->screen = __bare_screen();
        if (app->build) {
            app->build(app, app->screen);
        }
    }
    if (s_active && s_active->exit) {
        s_active->exit(s_active);
    }
    lv_screen_load(app->screen);
    s_active = app;
    if (app->enter) {
        app->enter(app);
    }
}

static void __tile_cb(lv_event_t *e)
{
    KALEIDO_APP_T *app = (KALEIDO_APP_T *)lv_event_get_user_data(e);
    if (app == &s_chat_facet) {
        lv_screen_load(s_chat_scr);
        s_active = &s_chat_facet;
        return;
    }
    __kaleido_open(app);
}


/***********************************************************
********************* public: helpers **********************
***********************************************************/
lv_obj_t *kaleido_header(lv_obj_t *root, const char *title)
{
    /* top bar */
    lv_obj_t *bar = lv_obj_create(root);
    lv_obj_set_size(bar, KAL_W, 44);
    lv_obj_set_pos(bar, 0, 0);
    lv_obj_set_style_bg_color(bar, KAL_COL_FACE, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(bar, 0, LV_PART_MAIN);
    lv_obj_remove_flag(bar, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *back = lv_obj_create(bar);
    lv_obj_set_size(back, 40, 32);
    lv_obj_align(back, LV_ALIGN_LEFT_MID, 2, 0);
    lv_obj_set_style_bg_color(back, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_set_style_radius(back, 8, LV_PART_MAIN);
    lv_obj_set_style_border_width(back, 0, LV_PART_MAIN);
    lv_obj_add_flag(back, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(back, __to_apps_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *bl = lv_label_create(back);
    lv_label_set_text(bl, LV_SYMBOL_LEFT);
    lv_obj_set_style_text_color(bl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_center(bl);

    lv_obj_t *tl = lv_label_create(bar);
    lv_label_set_text(tl, title ? title : "");
    lv_obj_set_style_text_color(tl, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_align(tl, LV_ALIGN_CENTER, 8, 0);

    /* body below the bar */
    lv_obj_t *body = lv_obj_create(root);
    lv_obj_set_size(body, KAL_W, KAL_H - 44);
    lv_obj_set_pos(body, 0, 44);
    lv_obj_set_style_bg_opa(body, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(body, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(body, 10, LV_PART_MAIN);
    return body;
}

void kaleido_toast(const char *msg)
{
    if (msg == NULL) {
        return;
    }
    kal_lock();
    lv_obj_t *t = lv_label_create(lv_layer_top());
    lv_label_set_text(t, msg);
    lv_obj_set_style_bg_color(t, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(t, LV_OPA_90, LV_PART_MAIN);
    lv_obj_set_style_text_color(t, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_set_style_pad_all(t, 8, LV_PART_MAIN);
    lv_obj_set_style_radius(t, 8, LV_PART_MAIN);
    lv_obj_align(t, LV_ALIGN_BOTTOM_MID, 0, -70);
    lv_obj_delete_delayed(t, 1800);
    kal_unlock();
}

/***********************************************************
********************* public: navigation *******************
***********************************************************/
void kaleido_register(KALEIDO_APP_T *app)
{
    if (app && s_app_cnt < KAL_MAX_APPS) {
        s_apps[s_app_cnt++] = app;
    }
}

void kaleido_home(void)      { kal_lock(); lv_screen_load(s_home_scr); s_active = NULL; kal_unlock(); }
void kaleido_open_apps(void) { kal_lock(); lv_screen_load(s_apps_scr); s_active = NULL; kal_unlock(); }
void kaleido_open_chat(void) { kal_lock(); lv_screen_load(s_chat_scr); s_active = &s_chat_facet; kal_unlock(); }
void kaleido_open_about(void){ kal_lock(); lv_screen_load(s_about_scr); s_active = NULL; kal_unlock(); }
void kaleido_open(KALEIDO_APP_T *app) { kal_lock(); __kaleido_open(app); kal_unlock(); }

/* Lock-free screen requests: safe from any context (including lv_timer
 * callbacks, where the display mutex is already held). __ui_poll_cb applies
 * them on the LVGL thread. The daemon's "open" command uses these. */
void kaleido_request_open(KALEIDO_APP_T *app) { s_open_req = app; }
void kaleido_request_apps(void)               { s_apps_req = 1; }
void kaleido_request_home(void)               { s_home_req = 1; }

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
            if (b == 0) return 1;      /* matched all of needle */
            if (a == 0) return 0;      /* ran off hay */
            if (a >= 'A' && a <= 'Z') a += 32;
            if (b >= 'A' && b <= 'Z') b += 32;
            if (a != b) break;
            i++;
        }
    }
    return 0;
}

void kaleido_on_voice(const char *text)
{
    if (text == NULL || text[0] == 0) {
        return;
    }

    /* Guardian listens for distress on every utterance, first. */
    app_guardian_on_text(text);

    /* --- Local voice commands (BMO reacts to what you ask) --- */
    if (__ci_contains(text, "who are you") || __ci_contains(text, "what are you") ||
        __ci_contains(text, "what can you do") || __ci_contains(text, "what do you do") ||
        __ci_contains(text, "your features") || __ci_contains(text, "what features") ||
        __ci_contains(text, "what tracks") || __ci_contains(text, "introduce")) {
        kaleido_open_about();     /* BMO shows off everything it can do */
        return;
    }
    if (__ci_contains(text, "menu") || __ci_contains(text, "show apps") ||
        __ci_contains(text, "the apps") || __ci_contains(text, "app grid")) {
        kaleido_open_apps();
        return;
    }
    if (__ci_contains(text, "go home") || __ci_contains(text, "your face") ||
        __ci_contains(text, "home screen")) {
        kaleido_home();
        return;
    }
    if (__ci_contains(text, "play") || __ci_contains(text, "game") || __ci_contains(text, "arcade")) {
        kaleido_open(&kaleido_app_arcade);
        return;
    }
    if (__ci_contains(text, "let's talk") || __ci_contains(text, "lets talk") ||
        __ci_contains(text, "open chat")) {
        kaleido_open_chat();
        return;
    }
    /* The two-board brain: the OrangePi running Armada. */
    if (__ci_contains(text, "brain") || __ci_contains(text, "daemon") ||
        __ci_contains(text, "orange")) {
        kaleido_open(&kaleido_app_brain);
        return;
    }
    /* Trader: PandaAI live trading signals. */
    if (__ci_contains(text, "trader") || __ci_contains(text, "trading") ||
        __ci_contains(text, "trade") || __ci_contains(text, "signal") ||
        __ci_contains(text, "oracle")) {
        kaleido_open(&kaleido_app_trader);
        return;
    }
    if (__ci_contains(text, "stepfun") || __ci_contains(text, "step fun") ||
        __ci_contains(text, "browser") || __ci_contains(text, "computer") ||
        __ci_contains(text, "my pc")) {
        kaleido_open(&kaleido_app_stepfun);
        return;
    }
    /* "open oracle", "show me the daemon", ... — match any facet by name. */
    for (int i = 0; i < s_app_cnt; i++) {
        if (s_apps[i] && s_apps[i]->name && __ci_contains(text, s_apps[i]->name)) {
            kaleido_open(s_apps[i]);
            return;
        }
    }

    /* Otherwise, let the active facet handle it. */
    if (s_active && s_active->on_voice) {
        kal_lock();
        s_active->on_voice(s_active, text);
        kal_unlock();
    }
}

/* Both of these are safe to call from ANY context (LVGL or not): they only set a
 * request flag; __ui_poll_cb does the actual LVGL work on the LVGL thread. */
void kaleido_face_set(const char *status_or_emotion)
{
    if (status_or_emotion == NULL) {
        return;
    }
    if (strstr(status_or_emotion, "LISTENING"))      s_face_req = APP_FACE_LISTENING;
    else if (strstr(status_or_emotion, "SPEAKING"))  s_face_req = APP_FACE_SPEAKING;
    else if (strstr(status_or_emotion, "happy"))     s_face_req = APP_FACE_HAPPY;
    else if (strstr(status_or_emotion, "sad"))       s_face_req = APP_FACE_SAD;
    else if (strstr(status_or_emotion, "surprise"))  s_face_req = APP_FACE_SURPRISED;
    else                                             s_face_req = APP_FACE_IDLE;
}

void kaleido_face_alert(void)
{
    s_face_req = APP_FACE_ALERT;
    s_home_req = 1; /* bring the alarming face to the front */
}

/***********************************************************
*********************** screen builders ********************
***********************************************************/
static void __build_home(void)
{
    s_home_scr = __bare_screen();
    /* match BMO's face screen so any rotation edge sliver blends in (no border) */
    lv_obj_set_style_bg_color(s_home_scr, lv_color_hex(0xCBEFDF), LV_PART_MAIN);

    /* BMO's face fills the entire screen. Navigation is by voice ("menu",
     * "who are you", a facet name, ...); touch is only used inside games and
     * facets, so the home screen carries no buttons. */
    s_face_area = lv_obj_create(s_home_scr);
    lv_obj_set_size(s_face_area, KAL_W, KAL_H);
    lv_obj_set_pos(s_face_area, 0, 0);
    lv_obj_set_style_pad_all(s_face_area, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(s_face_area, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_face_area, 0, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_face_area, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_remove_flag(s_face_area, LV_OBJ_FLAG_SCROLLABLE);
    app_face_init(s_face_area); /* draws the animated BMO face into this area */
}

/* Launcher tiles, in build order (Chat first, then the registered facets). */
static lv_obj_t *s_tiles[KAL_MAX_APPS + 1];
static int       s_tile_cnt = 0;

static void __build_tile(lv_obj_t *grid, KALEIDO_APP_T *app)
{
    /* Launcher tiles are tracked so the entrance stagger can replay on every
     * visit: the screen (and its tiles) is built once, the LV_EVENT_SCREEN_LOADED
     * hook in __build_apps restarts the anims each time it is shown. */
    lv_obj_t *tile = lv_obj_create(grid);
    lv_obj_set_size(tile, 200, 120);
    lv_obj_set_style_bg_color(tile, lv_color_hex(0x1E6A5E), LV_PART_MAIN);
    lv_obj_set_style_bg_color(tile, lv_color_hex(0x143F39), LV_STATE_PRESSED); /* darken on press */
    lv_obj_set_style_bg_opa(tile, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(tile, 18, LV_PART_MAIN);
    /* subtle glassy border: 1px white at ~12% opacity (brighter on press) */
    lv_obj_set_style_border_width(tile, 1, LV_PART_MAIN);
    lv_obj_set_style_border_color(tile, lv_color_hex(0xFFFFFF), LV_PART_MAIN);
    lv_obj_set_style_border_opa(tile, (lv_opa_t)(255 * 12 / 100), LV_PART_MAIN);
    lv_obj_set_style_border_opa(tile, LV_OPA_40, LV_STATE_PRESSED);
    lv_obj_set_style_pad_all(tile, 0, LV_PART_MAIN);
    lv_obj_remove_flag(tile, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(tile, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(tile, __tile_cb, LV_EVENT_CLICKED, app);

    /* soft rounded icon chip in the facet's tint, white glyph inside */
    lv_obj_t *chip = lv_obj_create(tile);
    lv_obj_set_size(chip, 48, 48);
    lv_obj_align(chip, LV_ALIGN_TOP_MID, 0, 14);
    lv_obj_set_style_bg_color(chip, lv_color_hex(app->tint), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(chip, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(chip, 14, LV_PART_MAIN);
    lv_obj_set_style_border_width(chip, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(chip, 0, LV_PART_MAIN);
    lv_obj_remove_flag(chip, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *icon = lv_label_create(chip);
    lv_label_set_text(icon, app->glyph ? app->glyph : "?");
    lv_obj_set_style_text_font(icon, &lv_font_montserrat_24, LV_PART_MAIN);
    lv_obj_set_style_text_color(icon, lv_color_hex(0xFFFFFF), LV_PART_MAIN);
    lv_obj_center(icon);

    lv_obj_t *nm = lv_label_create(tile);
    lv_label_set_text(nm, app->name);
    lv_obj_set_style_text_font(nm, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_set_style_text_color(nm, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(nm, LV_ALIGN_TOP_MID, 0, 70);

    /* track: dim, one clipped line */
    lv_obj_t *tr = lv_label_create(tile);
    lv_label_set_long_mode(tr, LV_LABEL_LONG_DOT);
    lv_obj_set_width(tr, 176);
    lv_label_set_text(tr, app->track ? app->track : "");
    lv_obj_set_style_text_align(tr, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_obj_set_style_text_color(tr, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_set_style_text_opa(tr, LV_OPA_60, LV_PART_MAIN);
    lv_obj_align(tr, LV_ALIGN_TOP_MID, 0, 92);

    if (s_tile_cnt < (int)(sizeof(s_tiles) / sizeof(s_tiles[0]))) {
        s_tiles[s_tile_cnt++] = tile;
    }
}

/* Entrance stagger: one 0..100 progress anim per tile, driving opacity and a
 * small rise. translate_y (not y) is animated because the tiles live in a
 * flex container that owns their coordinates. */
static void __tile_enter_cb(void *obj, int32_t v)
{
    lv_obj_set_style_translate_y((lv_obj_t *)obj, 12 - (int32_t)(12 * v / 100), LV_PART_MAIN);
    lv_obj_set_style_opa((lv_obj_t *)obj, (lv_opa_t)(255 * v / 100), LV_PART_MAIN);
}

static void __apps_loaded_cb(lv_event_t *e)
{
    (void)e;
    for (int i = 0; i < s_tile_cnt; i++) {
        lv_anim_delete(s_tiles[i], __tile_enter_cb);
        lv_obj_set_style_translate_y(s_tiles[i], 12, LV_PART_MAIN);
        lv_obj_set_style_opa(s_tiles[i], LV_OPA_TRANSP, LV_PART_MAIN);

        lv_anim_t a;
        lv_anim_init(&a);
        lv_anim_set_var(&a, s_tiles[i]);
        lv_anim_set_values(&a, 0, 100);
        lv_anim_set_time(&a, 250);
        lv_anim_set_delay(&a, (uint32_t)(i * 80)); /* ~80 ms stagger */
        lv_anim_set_exec_cb(&a, __tile_enter_cb);
        lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
        lv_anim_start(&a);
    }
}

static void __build_apps(void)
{
    s_apps_scr = __bare_screen();

    lv_obj_t *title = lv_label_create(s_apps_scr);
    lv_label_set_text(title, "Kaleidoscope");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_set_style_text_color(title, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 12);

    lv_obj_t *home = lv_obj_create(s_apps_scr);
    lv_obj_set_size(home, 40, 32);
    lv_obj_align(home, LV_ALIGN_TOP_LEFT, 6, 8);
    lv_obj_set_style_bg_color(home, KAL_COL_FACE, LV_PART_MAIN);
    lv_obj_set_style_radius(home, 8, LV_PART_MAIN);
    lv_obj_set_style_border_width(home, 0, LV_PART_MAIN);
    lv_obj_add_flag(home, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(home, __to_home_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *hl = lv_label_create(home);
    lv_label_set_text(hl, LV_SYMBOL_HOME);
    lv_obj_set_style_text_color(hl, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_center(hl);

    /* scrollable tile grid: two centered 200 px columns */
    lv_obj_t *grid = lv_obj_create(s_apps_scr);
    lv_obj_set_size(grid, KAL_W, KAL_H - 50);
    lv_obj_set_pos(grid, 0, 48);
    lv_obj_set_style_bg_opa(grid, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(grid, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(grid, 12, LV_PART_MAIN);
    lv_obj_set_style_pad_row(grid, 14, LV_PART_MAIN);
    lv_obj_set_style_pad_column(grid, 16, LV_PART_MAIN);
    lv_obj_set_flex_flow(grid, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_flex_align(grid, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

    __build_tile(grid, &s_chat_facet); /* Chat first */
    for (int i = 0; i < s_app_cnt; i++) {
        __build_tile(grid, s_apps[i]);
    }

    /* Build happens once; replay the entrance stagger on every visit. */
    lv_obj_add_event_cb(s_apps_scr, __apps_loaded_cb, LV_EVENT_SCREEN_LOADED, NULL);
}

/* ----- "Who am I": BMO introduces itself and every feature ----- */
static void __about_row(lv_obj_t *parent, KALEIDO_APP_T *app)
{
    if (app == NULL) {
        return;
    }
    lv_obj_t *row = lv_obj_create(parent);
    lv_obj_set_size(row, KAL_W - 26, 68);
    lv_obj_set_style_bg_color(row, lv_color_hex(app->tint), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(row, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(row, 12, LV_PART_MAIN);
    lv_obj_set_style_border_width(row, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(row, 8, LV_PART_MAIN);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(row, __tile_cb, LV_EVENT_CLICKED, app);

    lv_obj_t *nm = lv_label_create(row);
    lv_label_set_text_fmt(nm, "%s  %s", app->glyph ? app->glyph : "", app->name);
    lv_obj_set_style_text_color(nm, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_align(nm, LV_ALIGN_TOP_LEFT, 0, 0);

    lv_obj_t *ds = lv_label_create(row);
    lv_label_set_long_mode(ds, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(ds, KAL_W - 52);
    lv_label_set_text(ds, app->desc ? app->desc : "");
    lv_obj_set_style_text_color(ds, KAL_COL_BG, LV_PART_MAIN);
    lv_obj_align(ds, LV_ALIGN_TOP_LEFT, 0, 24);
}

static void __build_about(void)
{
    s_about_scr = __bare_screen();

    lv_obj_t *home = lv_obj_create(s_about_scr);
    lv_obj_set_size(home, 40, 32);
    lv_obj_align(home, LV_ALIGN_TOP_LEFT, 6, 8);
    lv_obj_set_style_bg_color(home, KAL_COL_FACE, LV_PART_MAIN);
    lv_obj_set_style_radius(home, 8, LV_PART_MAIN);
    lv_obj_set_style_border_width(home, 0, LV_PART_MAIN);
    lv_obj_add_flag(home, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(home, __to_home_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *hl = lv_label_create(home);
    lv_label_set_text(hl, LV_SYMBOL_HOME);
    lv_obj_set_style_text_color(hl, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_center(hl);

    lv_obj_t *title = lv_label_create(s_about_scr);
    lv_label_set_text(title, "Hi! I'm BMO");
    lv_obj_set_style_text_color(title, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 12, 10);

    lv_obj_t *sub = lv_label_create(s_about_scr);
    lv_label_set_long_mode(sub, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(sub, KAL_W - 24);
    lv_label_set_text(sub, "Here is everything I can do. Tap one to try it, or just say \"menu\" or \"play a game\".");
    lv_obj_set_style_text_color(sub, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(sub, LV_ALIGN_TOP_MID, 0, 40);

    /* scrollable feature list */
    lv_obj_t *list = lv_obj_create(s_about_scr);
    lv_obj_set_size(list, KAL_W, KAL_H - 96);
    lv_obj_set_pos(list, 0, 92);
    lv_obj_set_style_bg_opa(list, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(list, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(list, 8, LV_PART_MAIN);
    lv_obj_set_style_pad_row(list, 8, LV_PART_MAIN);
    lv_obj_set_flex_flow(list, LV_FLEX_FLOW_COLUMN);

    __about_row(list, &s_chat_facet);
    for (int i = 0; i < s_app_cnt; i++) {
        __about_row(list, s_apps[i]);
    }
}

/* ----- boot intro (edit me later) ----- */

/* Proper 2-arg anim callback for fading opacity (never cast the 3-arg style
 * setter to lv_anim_exec_xcb_t — the extra selector arg would be garbage). */
static void __opa_anim_cb(void *obj, int32_t v)
{
    lv_obj_set_style_opa((lv_obj_t *)obj, (lv_opa_t)v, LV_PART_MAIN);
}

static void __intro_done_cb(lv_timer_t *t)
{
    (void)t; /* repeat_count==1 auto-deletes this timer; don't delete it here */
    lv_screen_load(s_home_scr);
    s_active = NULL;
}

static void __build_intro(void)
{
    s_intro_scr = __bare_screen();
    lv_obj_set_style_bg_color(s_intro_scr, KAL_COL_FACE, LV_PART_MAIN);

    lv_obj_t *word = lv_label_create(s_intro_scr);
    lv_label_set_text(word, "KALEIDOSCOPE");
    lv_obj_set_style_text_color(word, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_align(word, LV_ALIGN_CENTER, 0, -10);

    lv_obj_t *sub = lv_label_create(s_intro_scr);
    lv_label_set_text(sub, "beep boop... waking up");
    lv_obj_set_style_text_color(sub, KAL_COL_BG, LV_PART_MAIN);
    lv_obj_align(sub, LV_ALIGN_CENTER, 0, 20);

    /* simple fade-in of the wordmark */
    lv_obj_set_style_opa(word, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, word);
    lv_anim_set_values(&a, LV_OPA_TRANSP, LV_OPA_COVER);
    lv_anim_set_time(&a, 900);
    lv_anim_set_exec_cb(&a, __opa_anim_cb);
    lv_anim_start(&a);
    /* TODO(you): swap in a nicer intro / a jingle via ai_audio_player_alert(). */
}

/***********************************************************
****************** inline placeholder facets ***************
***********************************************************/
/* These show a titled screen + the track they target, so every tile is present
 * and navigable now. Real logic lands in app_oracle.c / app_daemon.c /
 * app_courier.c next pass. */
static void __placeholder_build(KALEIDO_APP_T *self, lv_obj_t *root)
{
    lv_obj_t *body = kaleido_header(root, self->name);
    lv_obj_t *l = lv_label_create(body);
    lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(l, KAL_W - 40);
    lv_label_set_text_fmt(l, "%s\n\nTrack: %s\n\nThis facet is wired into the launcher.\nIts live logic comes online next.",
                          self->glyph ? self->glyph : "", self->track ? self->track : "");
    lv_obj_set_style_text_color(l, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(l, LV_ALIGN_TOP_LEFT, 0, 10);
}

static void __guardian_test_cb(lv_event_t *e)
{
    (void)e;
    app_guardian_on_text("help i fell"); /* fire the distress path for a demo */
}

static void __guardian_build(KALEIDO_APP_T *self, lv_obj_t *root)
{
    lv_obj_t *body = kaleido_header(root, self->name);
    lv_obj_t *l = lv_label_create(body);
    lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(l, KAL_W - 40);
    lv_label_set_text(l, "Armed. Listening for distress\nphrases like \"help\" or \"I fell\".\nOn a hit: red alarm face, alarm\ntone, and a push to your phone.");
    lv_obj_set_style_text_color(l, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(l, LV_ALIGN_TOP_LEFT, 0, 6);

    lv_obj_t *btn = lv_obj_create(body);
    lv_obj_set_size(btn, 180, 52);
    lv_obj_align(btn, LV_ALIGN_BOTTOM_MID, 0, -20);
    lv_obj_set_style_bg_color(btn, KAL_COL_ALERT, LV_PART_MAIN);
    lv_obj_set_style_radius(btn, 12, LV_PART_MAIN);
    lv_obj_set_style_border_width(btn, 0, LV_PART_MAIN);
    lv_obj_add_flag(btn, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(btn, __guardian_test_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *bl = lv_label_create(btn);
    lv_label_set_text(bl, "TEST SOS");
    lv_obj_set_style_text_color(bl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_center(bl);
}

/* StepFun track — the real facet is kaleido_app_stepfun in app_stepfun.c
 * (BMO -> Pi bridge :8101 -> PC browser agent :8200). Registered below. */
/* Photon track — teammate-owned. Placeholder; build the real feature into build(). */
static KALEIDO_APP_T s_facet_photon = {
    .name = "Photon", .track = "Photon", .glyph = LV_SYMBOL_ENVELOPE, .tint = 0xC79BF2,
    .desc = "Photon track \xC2\xB7 coming soon.",
    .build = __placeholder_build,
};
static KALEIDO_APP_T s_facet_guardian = {
    .name = "Guardian", .track = "Safety", .glyph = LV_SYMBOL_WARNING, .tint = 0xF29CA3,
    .desc = "I listen for \"help\" or \"I fell\" and alert someone who cares.",
    .build = __guardian_build,
};

/***********************************************************
*************************** boot ***************************
***********************************************************/
void kaleido_start(void)
{
    /* Bring up the distress watcher (not started elsewhere on this board). */
    app_guardian_init();

    /* Register the facets: real ones + placeholders. */
    kaleido_register(&s_facet_guardian);
    kaleido_register(&kaleido_app_arcade);
    kaleido_register(&kaleido_app_stepfun);
    kaleido_register(&s_facet_photon);

    kal_lock();
    /* The screen the ai_ui chat was drawn on becomes our "Chat" facet. */
    s_chat_scr = lv_screen_active();
    s_chat_facet.screen = s_chat_scr;

    __build_home();
    __build_apps();
    __build_about();
    __build_intro();

    lv_screen_load(s_intro_scr);
    lv_timer_t *t = lv_timer_create(__intro_done_cb, 2600, NULL);
    lv_timer_set_repeat_count(t, 1);

    /* Poll timer that applies cross-thread face/screen requests safely. */
    lv_timer_create(__ui_poll_cb, 120, NULL);
    kal_unlock();

    PR_NOTICE("kaleido shell started (%d facets)", s_app_cnt);
}
