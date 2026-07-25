/**
 * @file app_face.c
 * @brief BMO face (Adventure Time), drawn with LVGL 9 primitives — no image
 *        assets, so it costs almost no flash and scales to the panel.
 *
 * Landscape 480x320. BMO is: a pale-mint screen, two small dark eyes with a
 * glint, and the open grin — a black-outlined white mouth whose lower half is
 * filled EDGE-TO-EDGE by a teal tongue (bounded by the outline, white "teeth"
 * band on top). Idle BMO is still + blinks (like the show); the mouth only
 * opens/closes while it is actually speaking.
 */

#include "app_face.h"
#include "tal_api.h"
#include "lvgl.h"
#include <string.h>

/***********************************************************
******************** palette (BMO) *************************
***********************************************************/
#define COL_SCREEN    lv_color_hex(0xCBEFDF) // pale-mint BMO face screen
#define COL_INK       lv_color_hex(0x14352B) // near-black eyes + mouth outline
#define COL_TONGUE    lv_color_hex(0x34B39A) // teal tongue
#define COL_TEETH     lv_color_hex(0xF6FBF7) // off-white mouth / teeth
#define COL_GLINT     lv_color_hex(0xFFFFFF) // eye highlight
#define COL_ALERT     lv_color_hex(0xD0342C) // distress red
#define COL_ALERT_DK  lv_color_hex(0x7A1B16)
#define COL_BLUSH     lv_color_hex(0xF29CA3)

/* geometry, relative to the face-area centre */
#define EYE_W         42
#define EYE_H         50
#define EYE_DX        96   // each eye's horizontal offset from centre
#define EYE_DY        -54  // eyes sit above centre
#define BLINK_PERIOD  3400 // ms between idle blinks

#define MOUTH_W       238  // grin width
#define MOUTH_H       122  // default grin height
#define MOUTH_DY      50   // mouth below centre
#define MOUTH_BORDER  6    // black outline thickness
#define MOUTH_RADIUS  46   // rounded-rect corners
#define TONGUE_RADIUS 26   // tongue top-corner rounding
#define TONGUE_PCT    58   // tongue height as % of the mouth height

/***********************************************************
*********************** widget state ***********************
***********************************************************/
static lv_obj_t *s_scr    = NULL; // the face area (parent)
static lv_obj_t *s_eye_l  = NULL;
static lv_obj_t *s_eye_r  = NULL;
static lv_obj_t *s_glint_l = NULL;
static lv_obj_t *s_glint_r = NULL;
static lv_obj_t *s_blush_l = NULL;
static lv_obj_t *s_blush_r = NULL;
static lv_obj_t *s_mouth  = NULL; // white mouth (teeth base) + black outline
static lv_obj_t *s_tongue = NULL; // teal tongue filling the lower mouth

static lv_timer_t *s_blink_timer = NULL;
static lv_timer_t *s_alert_timer = NULL;

static APP_FACE_STATE_E s_state = APP_FACE_IDLE;

/***********************************************************
************************ eye helpers ***********************
***********************************************************/
static void eye_place(int32_t dy_extra)
{
    lv_obj_align(s_eye_l, LV_ALIGN_CENTER, -EYE_DX, EYE_DY + dy_extra);
    lv_obj_align(s_eye_r, LV_ALIGN_CENTER, EYE_DX, EYE_DY + dy_extra);
}

static void eye_h_anim(void *var, int32_t v)
{
    (void)var;
    lv_obj_set_height(s_eye_l, v);
    lv_obj_set_height(s_eye_r, v);
    eye_place(0);
}

static void do_blink(void)
{
    if (s_state == APP_FACE_SLEEP || s_state == APP_FACE_ALERT) {
        return;
    }
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_eye_l);
    lv_anim_set_exec_cb(&a, eye_h_anim);
    lv_anim_set_values(&a, EYE_H, 6);
    lv_anim_set_time(&a, 90);
    lv_anim_set_playback_time(&a, 120);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
    lv_anim_start(&a);
}

static void blink_timer_cb(lv_timer_t *t)
{
    (void)t;
    do_blink();
}

/***********************************************************
*********************** mouth helpers **********************
***********************************************************/
// Set the grin height h. The tongue fills the lower part edge-to-edge (full
// mouth width; clipped to the rounded outline), leaving a white teeth band up
// top. When h grows the mouth "opens".
static void mouth_set(int32_t h)
{
    if (h < 30) h = 30;
    lv_obj_set_size(s_mouth, MOUTH_W, h);
    lv_obj_align(s_mouth, LV_ALIGN_CENTER, 0, MOUTH_DY);

    int32_t th = h * TONGUE_PCT / 100;
    if (th < 12) th = 12;
    lv_obj_set_size(s_tongue, MOUTH_W, th);           // full width -> edge to edge
    lv_obj_align(s_tongue, LV_ALIGN_BOTTOM_MID, 0, 0);
}

static void mouth_anim_cb(void *var, int32_t v)
{
    (void)var;
    mouth_set(v);
}

// continuous open/close while talking
static void talk_start(void)
{
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_mouth);
    lv_anim_set_exec_cb(&a, mouth_anim_cb);
    lv_anim_set_values(&a, MOUTH_H - 22, MOUTH_H + 26);
    lv_anim_set_time(&a, 190);
    lv_anim_set_playback_time(&a, 170);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_start(&a);
}

/***********************************************************
*********************** alert helpers **********************
***********************************************************/
static void alert_timer_cb(lv_timer_t *t)
{
    (void)t;
    static bool on = false;
    on = !on;
    lv_obj_set_style_bg_color(s_scr, on ? COL_ALERT : COL_ALERT_DK, LV_PART_MAIN);
}

static void stop_timer(lv_timer_t **t)
{
    if (*t) {
        lv_timer_delete(*t);
        *t = NULL;
    }
}

/***********************************************************
************************ public API ************************
***********************************************************/
OPERATE_RET app_face_init(lv_obj_t *parent)
{
    if (parent == NULL) {
        return OPRT_INVALID_PARM;
    }
    s_scr = parent;
    lv_obj_set_style_bg_color(s_scr, COL_SCREEN, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_scr, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_scr, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(s_scr, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_scr, 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_scr, LV_OBJ_FLAG_SCROLLABLE);

    // ---- eyes (small dark ovals, each with a white glint) ----
    for (int i = 0; i < 2; i++) {
        lv_obj_t *eye = lv_obj_create(s_scr);
        lv_obj_set_size(eye, EYE_W, EYE_H);
        lv_obj_set_style_radius(eye, EYE_W / 2, LV_PART_MAIN);
        lv_obj_set_style_bg_color(eye, COL_INK, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(eye, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_border_width(eye, 0, LV_PART_MAIN);
        lv_obj_set_style_pad_all(eye, 0, LV_PART_MAIN);
        lv_obj_remove_flag(eye, LV_OBJ_FLAG_SCROLLABLE);

        lv_obj_t *glint = lv_obj_create(eye);
        lv_obj_set_size(glint, 13, 13);
        lv_obj_set_style_radius(glint, 7, LV_PART_MAIN);
        lv_obj_set_style_bg_color(glint, COL_GLINT, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(glint, LV_OPA_90, LV_PART_MAIN);
        lv_obj_set_style_border_width(glint, 0, LV_PART_MAIN);
        lv_obj_remove_flag(glint, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_align(glint, LV_ALIGN_TOP_RIGHT, -7, 8);

        if (i == 0) { s_eye_l = eye; s_glint_l = glint; }
        else        { s_eye_r = eye; s_glint_r = glint; }
    }
    eye_place(0);

    // ---- blush (hidden unless happy) ----
    for (int i = 0; i < 2; i++) {
        lv_obj_t *b = lv_obj_create(s_scr);
        lv_obj_set_size(b, 32, 15);
        lv_obj_set_style_radius(b, 8, LV_PART_MAIN);
        lv_obj_set_style_bg_color(b, COL_BLUSH, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(b, LV_OPA_70, LV_PART_MAIN);
        lv_obj_set_style_border_width(b, 0, LV_PART_MAIN);
        lv_obj_remove_flag(b, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(b, LV_OBJ_FLAG_HIDDEN);
        lv_obj_align(b, LV_ALIGN_CENTER, i == 0 ? -(EYE_DX + 2) : (EYE_DX + 2), EYE_DY + 34);
        if (i == 0) s_blush_l = b; else s_blush_r = b;
    }

    // ---- mouth: white teeth base + black outline ----
    s_mouth = lv_obj_create(s_scr);
    lv_obj_set_size(s_mouth, MOUTH_W, MOUTH_H);
    lv_obj_set_style_radius(s_mouth, MOUTH_RADIUS, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_mouth, COL_TEETH, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_mouth, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_color(s_mouth, COL_INK, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_mouth, MOUTH_BORDER, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_mouth, 0, LV_PART_MAIN);
    lv_obj_set_style_clip_corner(s_mouth, true, LV_PART_MAIN);
    lv_obj_remove_flag(s_mouth, LV_OBJ_FLAG_SCROLLABLE);

    // ---- tongue: teal, fills the lower mouth edge-to-edge ----
    s_tongue = lv_obj_create(s_mouth);
    lv_obj_set_style_bg_color(s_tongue, COL_TONGUE, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_tongue, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_tongue, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(s_tongue, TONGUE_RADIUS, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_tongue, 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_tongue, LV_OBJ_FLAG_SCROLLABLE);

    mouth_set(MOUTH_H);

    s_blink_timer = lv_timer_create(blink_timer_cb, BLINK_PERIOD, NULL);

    app_face_set_state(APP_FACE_IDLE);
    return OPRT_OK;
}

void app_face_set_state(APP_FACE_STATE_E state)
{
    if (s_scr == NULL) {
        return;
    }
    s_state = state;

    // Reset to the neutral grin; stop any running mouth animation.
    lv_anim_delete(s_mouth, mouth_anim_cb);
    stop_timer(&s_alert_timer);
    lv_obj_add_flag(s_blush_l, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_blush_r, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(s_tongue, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_style_bg_color(s_scr, COL_SCREEN, LV_PART_MAIN);
    lv_obj_set_height(s_eye_l, EYE_H);
    lv_obj_set_height(s_eye_r, EYE_H);
    lv_obj_clear_flag(s_glint_l, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(s_glint_r, LV_OBJ_FLAG_HIDDEN);
    eye_place(0);
    mouth_set(MOUTH_H);

    switch (state) {
    case APP_FACE_LISTENING:
        eye_place(-8);              // look up, attentive
        break;

    case APP_FACE_THINKING:
        lv_obj_align(s_eye_l, LV_ALIGN_CENTER, -EYE_DX + 16, EYE_DY); // glance aside
        lv_obj_align(s_eye_r, LV_ALIGN_CENTER,  EYE_DX + 16, EYE_DY);
        break;

    case APP_FACE_SPEAKING:
        talk_start();               // continuous open/close
        break;

    case APP_FACE_HAPPY:
        lv_obj_clear_flag(s_blush_l, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_blush_r, LV_OBJ_FLAG_HIDDEN);
        mouth_set(MOUTH_H + 12);
        break;

    case APP_FACE_SAD:
        eye_place(8);               // droop
        lv_obj_add_flag(s_tongue, LV_OBJ_FLAG_HIDDEN);
        mouth_set(32);              // nearly closed
        break;

    case APP_FACE_SURPRISED:
        mouth_set(MOUTH_H + 36);    // wide open
        break;

    case APP_FACE_ALERT:
        mouth_set(MOUTH_H + 26);
        lv_obj_set_style_bg_color(s_scr, COL_ALERT, LV_PART_MAIN);
        s_alert_timer = lv_timer_create(alert_timer_cb, 350, NULL);
        break;

    case APP_FACE_SLEEP:
        lv_obj_set_height(s_eye_l, 6); // closed
        lv_obj_set_height(s_eye_r, 6);
        lv_obj_add_flag(s_glint_l, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_glint_r, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_tongue, LV_OBJ_FLAG_HIDDEN);
        eye_place(0);
        mouth_set(28);
        break;

    case APP_FACE_IDLE:
    default:
        break;
    }
}

void app_face_set_by_name(const char *name)
{
    if (name == NULL) {
        return;
    }
    // Device status strings from the chat app.
    if (0 == strcmp(name, "LISTENING")) { app_face_set_state(APP_FACE_LISTENING); return; }
    if (0 == strcmp(name, "SPEAKING"))  { app_face_set_state(APP_FACE_SPEAKING);  return; }
    if (0 == strcmp(name, "STANDBY"))   { app_face_set_state(APP_FACE_IDLE);      return; }

    // Emotion names (best-effort substring match, so "happy"/"very happy" etc. work).
    if (strstr(name, "happy") || strstr(name, "smile") || strstr(name, "laugh")) { app_face_set_state(APP_FACE_HAPPY); return; }
    if (strstr(name, "sad")   || strstr(name, "cry")   || strstr(name, "sorry")) { app_face_set_state(APP_FACE_SAD); return; }
    if (strstr(name, "surprise") || strstr(name, "shock") || strstr(name, "wow")) { app_face_set_state(APP_FACE_SURPRISED); return; }
    if (strstr(name, "sleep") || strstr(name, "neutral")) { app_face_set_state(APP_FACE_IDLE); return; }

    app_face_set_state(APP_FACE_IDLE);
}
