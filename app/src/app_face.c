/**
 * @file app_face.c
 * @brief Expressive BMO face drawn with lightweight LVGL 9 primitives.
 *
 * BMO's recognisable face is deliberately simple: two dark oval eyes and a
 * small curved mouth on a pale mint screen. Closed expressions use an LVGL arc;
 * open mouths appear only for speech, surprise and alerts. During TTS, a short
 * viseme sequence uses BMO's joyful curved eyes and notched, heart-like mouth
 * with a green lower lip so it feels like the character is forming sounds.
 */

#include "app_face.h"
#include "tal_api.h"
#include "lvgl.h"
#include <string.h>

/***********************************************************
******************** palette (BMO) *************************
***********************************************************/
#define COL_SCREEN    lv_color_hex(0xC7F0DF) /* softly lit mint LCD */
#define COL_INK       lv_color_hex(0x123B35) /* canonical dark teal line art */
#define COL_MOUTH     lv_color_hex(0x102E2A) /* open-mouth interior */
#define COL_TEETH     lv_color_hex(0xF8FFF9)
#define COL_TONGUE    lv_color_hex(0x73C982) /* green lower lip from BMO's happy pose */
#define COL_ALERT     lv_color_hex(0xDE5A54)
#define COL_ALERT_DK  lv_color_hex(0xA63431)
#define COL_BLUSH     lv_color_hex(0xF3A3A8)

/* Face geometry for the 480x320 landscape panel. */
#define EYE_W          32
#define EYE_H          42
#define EYE_DX         91
#define EYE_DY        -48
#define BLINK_PERIOD 3380

#define SMILE_W        112
#define SMILE_H         68
#define SMILE_DY        48
#define SMILE_STROKE     7

#define TALK_PERIOD    115

/***********************************************************
*********************** widget state ***********************
***********************************************************/
static lv_obj_t *s_scr = NULL;
static lv_obj_t *s_eye_l = NULL;
static lv_obj_t *s_eye_r = NULL;
static lv_obj_t *s_eye_smile_l = NULL;
static lv_obj_t *s_eye_smile_r = NULL;
static lv_obj_t *s_blush_l = NULL;
static lv_obj_t *s_blush_r = NULL;

/* Closed mouth and open mouth are separate objects. */
static lv_obj_t *s_smile = NULL;
static lv_obj_t *s_mouth = NULL;
static lv_obj_t *s_teeth = NULL;
static lv_obj_t *s_tongue = NULL;
static lv_obj_t *s_mouth_notch = NULL;

static lv_timer_t *s_blink_timer = NULL;
static lv_timer_t *s_talk_timer = NULL;
static lv_timer_t *s_alert_timer = NULL;

static APP_FACE_STATE_E s_state = APP_FACE_IDLE;
static APP_FACE_STATE_E s_rest_state = APP_FACE_IDLE;
static uint8_t s_talk_step = 0;

/***********************************************************
************************ utilities *************************
***********************************************************/
static void obj_show(lv_obj_t *obj, bool show)
{
    if (show) {
        lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
    }
}

static void stop_timer(lv_timer_t **timer)
{
    if (*timer) {
        lv_timer_delete(*timer);
        *timer = NULL;
    }
}

/***********************************************************
*************************** eyes ****************************
***********************************************************/
static void eye_place(int32_t x_shift, int32_t y_shift)
{
    lv_obj_align(s_eye_l, LV_ALIGN_CENTER, -EYE_DX + x_shift, EYE_DY + y_shift);
    lv_obj_align(s_eye_r, LV_ALIGN_CENTER,  EYE_DX + x_shift, EYE_DY + y_shift);
}

static void eye_set_size(int32_t width, int32_t height)
{
    lv_obj_set_size(s_eye_l, width, height);
    lv_obj_set_size(s_eye_r, width, height);
    lv_obj_set_style_radius(s_eye_l, width / 2, LV_PART_MAIN);
    lv_obj_set_style_radius(s_eye_r, width / 2, LV_PART_MAIN);
    eye_place(0, 0);
}

static void eye_h_anim(void *var, int32_t height)
{
    (void)var;
    lv_obj_set_height(s_eye_l, height);
    lv_obj_set_height(s_eye_r, height);
    eye_place(0, 0);
}

static void do_blink(void)
{
    if (s_state == APP_FACE_SLEEP || s_state == APP_FACE_ALERT ||
        s_state == APP_FACE_SPEAKING) {
        return;
    }

    lv_anim_t anim;
    lv_anim_init(&anim);
    lv_anim_set_var(&anim, s_eye_l);
    lv_anim_set_exec_cb(&anim, eye_h_anim);
    lv_anim_set_values(&anim, lv_obj_get_height(s_eye_l), 4);
    lv_anim_set_time(&anim, 75);
    lv_anim_set_playback_time(&anim, 105);
    lv_anim_set_path_cb(&anim, lv_anim_path_ease_in_out);
    lv_anim_start(&anim);
}

static void blink_timer_cb(lv_timer_t *timer)
{
    (void)timer;
    do_blink();
}

/***********************************************************
************************** mouth ****************************
***********************************************************/
/* LVGL angles run clockwise: 20..160 draws the friendly lower U curve;
 * 200..340 draws the upper arc used for a frown. */
static void mouth_curve(int32_t start, int32_t end, int32_t width,
                        int32_t height, int32_t x, int32_t y)
{
    obj_show(s_mouth, false);
    obj_show(s_mouth_notch, false);
    obj_show(s_smile, true);
    lv_obj_set_size(s_smile, width, height);
    lv_obj_align(s_smile, LV_ALIGN_CENTER, x, y);
    lv_arc_set_bg_angles(s_smile, start, end);
}

static void mouth_open(int32_t width, int32_t height, int32_t x, int32_t y,
                       bool teeth, bool tongue)
{
    if (width < 30) width = 30;
    if (height < 24) height = 24;

    obj_show(s_smile, false);
    obj_show(s_mouth, true);
    obj_show(s_mouth_notch, false);
    lv_obj_set_size(s_mouth, width, height);
    lv_obj_set_style_radius(s_mouth, height / 2, LV_PART_MAIN);
    lv_obj_align(s_mouth, LV_ALIGN_CENTER, x, y);

    int32_t inset = 7;
    int32_t inner_w = width - inset * 2;
    if (inner_w < 18) inner_w = 18;

    obj_show(s_teeth, teeth);
    if (teeth) {
        int32_t teeth_h = height / 4;
        if (teeth_h < 7) teeth_h = 7;
        if (teeth_h > 15) teeth_h = 15;
        lv_obj_set_size(s_teeth, inner_w, teeth_h);
        lv_obj_set_style_radius(s_teeth, teeth_h / 2, LV_PART_MAIN);
        lv_obj_align(s_teeth, LV_ALIGN_TOP_MID, 0, 5);
    }

    obj_show(s_tongue, tongue);
    if (tongue) {
        int32_t tongue_w = width * 78 / 100;
        int32_t tongue_h = height / 4;
        if (tongue_h < 9) tongue_h = 9;
        if (tongue_h > 18) tongue_h = 18;
        lv_obj_set_size(s_tongue, tongue_w, tongue_h);
        lv_obj_set_style_radius(s_tongue, tongue_h / 2, LV_PART_MAIN);
        lv_obj_align(s_tongue, LV_ALIGN_BOTTOM_MID, 0, -5);
    }
}

/* BMO's joyful speaking mouth is a compact open oval with a mint cutout at the
 * top. The cutout creates the characteristic heart-shaped centre notch while
 * the green lower lip remains clipped inside the dark mouth. */
static void mouth_heart_open(int32_t width, int32_t height, int32_t x, int32_t y)
{
    mouth_open(width, height, x, y, false, true);

    int32_t notch_w = width * 27 / 100;
    int32_t notch_h = height * 30 / 100;
    if (notch_w < 14) notch_w = 14;
    if (notch_h < 10) notch_h = 10;
    lv_obj_set_size(s_mouth_notch, notch_w, notch_h);
    lv_obj_set_style_radius(s_mouth_notch, notch_w / 2, LV_PART_MAIN);
    lv_obj_align(s_mouth_notch, LV_ALIGN_CENTER, x, y - height / 2 + 1);
    obj_show(s_mouth_notch, true);
    lv_obj_move_foreground(s_mouth_notch);
}

typedef struct {
    uint8_t width;
    uint8_t height;
    int8_t  x;
    int8_t  y;
} BMO_VISEME_T;

/* Uneven, compact speech shapes keep the joyful notched silhouette rather than
 * turning BMO's mouth into a large generic rounded rectangle. */
static const BMO_VISEME_T k_visemes[] = {
    { 52, 28,  0, 51 },
    { 66, 42, -1, 52 },
    { 78, 58,  1, 51 },
    { 70, 38,  0, 53 },
    { 60, 51,  1, 52 },
    { 84, 48, -1, 51 },
    { 58, 32,  0, 52 },
};

static void talk_timer_cb(lv_timer_t *timer)
{
    (void)timer;
    const BMO_VISEME_T *v = &k_visemes[s_talk_step];
    mouth_heart_open(v->width, v->height, v->x, v->y);
    s_talk_step = (uint8_t)((s_talk_step + 1) %
                            (sizeof(k_visemes) / sizeof(k_visemes[0])));
}

static void talk_start(void)
{
    stop_timer(&s_talk_timer);
    s_talk_step = 2;
    talk_timer_cb(NULL);
    s_talk_timer = lv_timer_create(talk_timer_cb, TALK_PERIOD, NULL);
}

/***********************************************************
************************** alerts ***************************
***********************************************************/
static void alert_timer_cb(lv_timer_t *timer)
{
    (void)timer;
    static bool bright = false;
    bright = !bright;
    lv_obj_set_style_bg_color(s_scr, bright ? COL_ALERT : COL_ALERT_DK, LV_PART_MAIN);
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

    /* Canonical BMO eyes: simple dark ovals, with no glossy highlights. */
    for (int i = 0; i < 2; i++) {
        lv_obj_t *eye = lv_obj_create(s_scr);
        lv_obj_set_size(eye, EYE_W, EYE_H);
        lv_obj_set_style_radius(eye, EYE_W / 2, LV_PART_MAIN);
        lv_obj_set_style_bg_color(eye, COL_INK, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(eye, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_border_width(eye, 0, LV_PART_MAIN);
        lv_obj_set_style_pad_all(eye, 0, LV_PART_MAIN);
        lv_obj_remove_flag(eye, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_remove_flag(eye, LV_OBJ_FLAG_SCROLLABLE);
        if (i == 0) s_eye_l = eye; else s_eye_r = eye;
    }
    eye_place(0, 0);

    /* The reference happy-talking eyes: a tiny central oval sitting above a
     * short curved stroke. They are swapped in only while BMO is speaking. */
    for (int i = 0; i < 2; i++) {
        lv_obj_t *curve = lv_arc_create(s_scr);
        lv_obj_set_size(curve, 37, 25);
        lv_obj_set_style_bg_opa(curve, LV_OPA_TRANSP, LV_PART_MAIN);
        lv_obj_set_style_arc_color(curve, COL_INK, LV_PART_MAIN);
        lv_obj_set_style_arc_width(curve, 4, LV_PART_MAIN);
        lv_obj_set_style_arc_rounded(curve, true, LV_PART_MAIN);
        lv_obj_set_style_arc_opa(curve, LV_OPA_TRANSP, LV_PART_INDICATOR);
        lv_arc_set_bg_angles(curve, 200, 340);
        lv_obj_remove_style(curve, NULL, LV_PART_KNOB);
        lv_obj_remove_flag(curve, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_remove_flag(curve, LV_OBJ_FLAG_SCROLLABLE);
        obj_show(curve, false);
        lv_obj_align(curve, LV_ALIGN_CENTER,
                     i == 0 ? -EYE_DX : EYE_DX, EYE_DY + 7);
        if (i == 0) s_eye_smile_l = curve; else s_eye_smile_r = curve;
    }

    /* Soft blush is reserved for BMO's happy expression. */
    for (int i = 0; i < 2; i++) {
        lv_obj_t *blush = lv_obj_create(s_scr);
        lv_obj_set_size(blush, 29, 12);
        lv_obj_set_style_radius(blush, 6, LV_PART_MAIN);
        lv_obj_set_style_bg_color(blush, COL_BLUSH, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(blush, LV_OPA_60, LV_PART_MAIN);
        lv_obj_set_style_border_width(blush, 0, LV_PART_MAIN);
        lv_obj_set_style_pad_all(blush, 0, LV_PART_MAIN);
        lv_obj_remove_flag(blush, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_remove_flag(blush, LV_OBJ_FLAG_SCROLLABLE);
        obj_show(blush, false);
        lv_obj_align(blush, LV_ALIGN_CENTER,
                     i == 0 ? -(EYE_DX + 2) : (EYE_DX + 2), EYE_DY + 31);
        if (i == 0) s_blush_l = blush; else s_blush_r = blush;
    }

    /* Closed smile: only the arc background is visible. */
    s_smile = lv_arc_create(s_scr);
    lv_obj_set_size(s_smile, SMILE_W, SMILE_H);
    lv_obj_set_style_bg_opa(s_smile, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_color(s_smile, COL_INK, LV_PART_MAIN);
    lv_obj_set_style_arc_width(s_smile, SMILE_STROKE, LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(s_smile, true, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(s_smile, LV_OPA_TRANSP, LV_PART_INDICATOR);
    lv_obj_remove_style(s_smile, NULL, LV_PART_KNOB);
    lv_obj_remove_flag(s_smile, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(s_smile, LV_OBJ_FLAG_SCROLLABLE);

    /* Open mouth and its optional teeth/tongue layers. */
    s_mouth = lv_obj_create(s_scr);
    lv_obj_set_style_bg_color(s_mouth, COL_MOUTH, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_mouth, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_mouth, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_mouth, 0, LV_PART_MAIN);
    lv_obj_set_style_clip_corner(s_mouth, true, LV_PART_MAIN);
    lv_obj_remove_flag(s_mouth, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(s_mouth, LV_OBJ_FLAG_SCROLLABLE);

    s_teeth = lv_obj_create(s_mouth);
    lv_obj_set_style_bg_color(s_teeth, COL_TEETH, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_teeth, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_teeth, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_teeth, 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_teeth, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(s_teeth, LV_OBJ_FLAG_SCROLLABLE);

    s_tongue = lv_obj_create(s_mouth);
    lv_obj_set_style_bg_color(s_tongue, COL_TONGUE, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_tongue, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_tongue, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_tongue, 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_tongue, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(s_tongue, LV_OBJ_FLAG_SCROLLABLE);

    /* Mint-coloured sibling used to cut the centre notch into speaking mouths. */
    s_mouth_notch = lv_obj_create(s_scr);
    lv_obj_set_style_bg_color(s_mouth_notch, COL_SCREEN, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_mouth_notch, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_mouth_notch, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_mouth_notch, 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_mouth_notch, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(s_mouth_notch, LV_OBJ_FLAG_SCROLLABLE);
    obj_show(s_mouth_notch, false);

    s_blink_timer = lv_timer_create(blink_timer_cb, BLINK_PERIOD, NULL);
    app_face_set_state(APP_FACE_IDLE);
    return OPRT_OK;
}

void app_face_set_state(APP_FACE_STATE_E state)
{
    if (s_scr == NULL) {
        return;
    }

    /* TTS temporarily overlays the current feeling. When speech stops and the
     * chat layer asks for IDLE, return to the expression BMO had beforehand. */
    if (state == APP_FACE_IDLE && s_state == APP_FACE_SPEAKING) {
        state = s_rest_state;
    } else if (state != APP_FACE_SPEAKING) {
        s_rest_state = state;
    }
    s_state = state;
    stop_timer(&s_talk_timer);
    stop_timer(&s_alert_timer);
    lv_anim_delete(s_eye_l, eye_h_anim);

    obj_show(s_blush_l, false);
    obj_show(s_blush_r, false);
    obj_show(s_eye_smile_l, false);
    obj_show(s_eye_smile_r, false);
    obj_show(s_mouth_notch, false);
    lv_obj_set_style_bg_color(s_scr, COL_SCREEN, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_eye_l, COL_INK, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_eye_r, COL_INK, LV_PART_MAIN);
    eye_set_size(EYE_W, EYE_H);
    eye_place(0, 0);
    mouth_curve(20, 160, SMILE_W, SMILE_H, 0, SMILE_DY);

    switch (state) {
    case APP_FACE_LISTENING:
        eye_set_size(EYE_W + 4, EYE_H + 5);
        eye_place(0, -5);
        mouth_open(44, 38, 0, 53, false, false);
        break;

    case APP_FACE_THINKING:
        eye_place(13, -2);
        mouth_curve(30, 132, 76, 50, -7, 54);
        break;

    case APP_FACE_SPEAKING:
        eye_set_size(11, 18);
        eye_place(0, -4);
        obj_show(s_eye_smile_l, true);
        obj_show(s_eye_smile_r, true);
        talk_start();
        break;

    case APP_FACE_HAPPY:
        obj_show(s_blush_l, true);
        obj_show(s_blush_r, true);
        mouth_curve(12, 168, 142, 84, 0, 43);
        break;

    case APP_FACE_SAD:
        eye_place(0, 7);
        mouth_curve(200, 340, 94, 54, 0, 73);
        break;

    case APP_FACE_SURPRISED:
        eye_set_size(EYE_W + 4, EYE_H + 9);
        eye_place(0, -4);
        mouth_open(58, 72, 0, 58, false, true);
        break;

    case APP_FACE_ALERT:
        lv_obj_set_style_bg_color(s_scr, COL_ALERT, LV_PART_MAIN);
        lv_obj_set_style_bg_color(s_eye_l, COL_TEETH, LV_PART_MAIN);
        lv_obj_set_style_bg_color(s_eye_r, COL_TEETH, LV_PART_MAIN);
        eye_set_size(EYE_W + 5, EYE_H + 9);
        mouth_open(92, 76, 0, 58, true, true);
        s_alert_timer = lv_timer_create(alert_timer_cb, 340, NULL);
        break;

    case APP_FACE_SLEEP:
        eye_set_size(48, 5);
        eye_place(0, 3);
        mouth_curve(28, 152, 74, 48, 0, 53);
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

    if (0 == strcmp(name, "LISTENING")) {
        app_face_set_state(APP_FACE_LISTENING);
        return;
    }
    if (0 == strcmp(name, "SPEAKING")) {
        app_face_set_state(APP_FACE_SPEAKING);
        return;
    }
    if (0 == strcmp(name, "THINKING")) {
        app_face_set_state(APP_FACE_THINKING);
        return;
    }
    if (0 == strcmp(name, "STANDBY")) {
        app_face_set_state(APP_FACE_IDLE);
        return;
    }

    if (strstr(name, "happy") || strstr(name, "smile") || strstr(name, "laugh")) {
        app_face_set_state(APP_FACE_HAPPY);
        return;
    }
    if (strstr(name, "sad") || strstr(name, "cry") || strstr(name, "sorry")) {
        app_face_set_state(APP_FACE_SAD);
        return;
    }
    if (strstr(name, "surprise") || strstr(name, "shock") || strstr(name, "wow")) {
        app_face_set_state(APP_FACE_SURPRISED);
        return;
    }
    if (strstr(name, "think") || strstr(name, "curious")) {
        app_face_set_state(APP_FACE_THINKING);
        return;
    }
    if (strstr(name, "sleep")) {
        app_face_set_state(APP_FACE_SLEEP);
        return;
    }
    if (strstr(name, "alert") || strstr(name, "angry")) {
        app_face_set_state(APP_FACE_ALERT);
        return;
    }

    app_face_set_state(APP_FACE_IDLE);
}
