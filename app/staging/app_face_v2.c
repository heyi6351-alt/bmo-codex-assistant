/**
 * @file app_face_v2.c
 * @brief BMO's face, v2 — a more authentic Adventure-Time BMO, drawn with LVGL 9
 *        vector primitives only (no image assets to flash).
 *
 * Drop-in replacement for app_face.c: same public API (app_face.h), same
 * APP_FACE_* states, no new headers required by callers.
 *
 * What changed vs v1
 *   - A real "face plate": a rounded mint screen inset in the teal body, so the
 *     face reads as BMO's front panel instead of a flat colour wash.
 *   - Rounder BMO-style eyes with a soft glint, a small nose dot, and a wide,
 *     shallow, friendly smile (big-radius arc, not a tight semicircle).
 *   - Natural blinking: randomised cadence plus the occasional double blink, and
 *     an idle "look around" glance so the face never feels frozen.
 *   - Per-state eyebrows (lv_line), rosy cheeks, a wavy worried mouth for ALERT,
 *     drifting "z" for SLEEP, and a rhythmic (non-robotic) talking mouth.
 *   - All geometry is derived from the parent's size, so it scales instead of
 *     assuming a pixel layout. Falls back to 320x480 if the parent has no size
 *     yet.
 *
 * Threading: every function here must be called from LVGL context / under the
 * LVGL mutex, exactly like v1. Nothing here calls back into the shell.
 */

#include "app_face.h"
#include "tal_api.h"
#include "lvgl.h"
#include <stdlib.h>
#include <string.h>

/***********************************************************
********************* palette (BMO) ************************
***********************************************************/
#define COL_BODY      lv_color_hex(0x1E8E82) /* body / backdrop behind the plate */
#define COL_PLATE     lv_color_hex(0x3FC3AC) /* mint face screen                  */
#define COL_PLATE_EDG lv_color_hex(0x16776C) /* bezel around the face screen      */
#define COL_INK       lv_color_hex(0x0B2E2A) /* eyes / nose / mouth               */
#define COL_ALERT     lv_color_hex(0xD0342C)
#define COL_ALERT_DK  lv_color_hex(0x7A1B16)
#define COL_BLUSH     lv_color_hex(0xF0808F)
#define COL_GLINT     lv_color_hex(0xFFFFFF)

/* fallback panel size when the parent has not been laid out yet */
#define FALLBACK_W    320
#define FALLBACK_H    420

#define BLINK_MIN_MS  2200
#define BLINK_VAR_MS  3000
#define TALK_MS       120

/***********************************************************
*********************** widget state ***********************
***********************************************************/
static lv_obj_t *s_root  = NULL; /* caller's container (body colour)   */
static lv_obj_t *s_plate = NULL; /* mint face screen; parent of all features */

static lv_obj_t *s_eye[2]   = {NULL, NULL};
static lv_obj_t *s_glint[2] = {NULL, NULL};
static lv_obj_t *s_brow[2]  = {NULL, NULL};
static lv_obj_t *s_blush[2] = {NULL, NULL};
static lv_obj_t *s_z[2]     = {NULL, NULL};
static lv_obj_t *s_smile    = NULL; /* arc: smile / frown            */
static lv_obj_t *s_mouth_o  = NULL; /* ellipse: open / talking mouth */
static lv_obj_t *s_wave     = NULL; /* zigzag worried mouth (ALERT)  */
static lv_obj_t *s_nose     = NULL;

static lv_timer_t *s_blink_timer = NULL;
static lv_timer_t *s_talk_timer  = NULL;
static lv_timer_t *s_alert_timer = NULL;

static APP_FACE_STATE_E s_state = APP_FACE_IDLE;

/* live geometry, computed once in app_face_init() from the parent size */
static int32_t PW, PH;          /* face plate size                      */
static int32_t EYE_W, EYE_H;    /* nominal (fully open) eye size        */
static int32_t EYE_DX, EYE_CY;  /* eye centre offsets from plate centre */
static int32_t MOUTH_CY, MOUTH_W, ARC_R, BROW_W, BROW_H, WAVE_W;
static int32_t Z_Y;

/* current eye pose (driven by animations, applied by eyes_apply) */
static int32_t s_eye_h    = 40;
static int32_t s_eye_w    = 40;
static int32_t s_eye_base = 40; /* the resting height for the current state */
static int32_t s_eye_dx   = 0;  /* glance left/right */
static int32_t s_eye_dy   = 0;  /* look up/down      */
static int32_t s_lid_drop = 0;  /* how far the "lid" pulls the eye down */
static int32_t s_lid_base = 0;  /* the state's own lid droop (sad/sleepy)  */

/* line point storage must outlive the call: LVGL keeps the pointer. */
static lv_point_precise_t s_brow_pts[2][2];
static lv_point_precise_t s_wave_pts[5];

static int s_seeded = 0;

static int32_t rnd(int32_t span)
{
    if (span <= 0) {
        return 0;
    }
    if (!s_seeded) {
        srand((unsigned)tal_system_get_millisecond());
        s_seeded = 1;
    }
    return (int32_t)(rand() % span);
}

/***********************************************************
************************ eyes ******************************
***********************************************************/
static void eyes_apply(void)
{
    for (int i = 0; i < 2; i++) {
        int32_t dx = (i == 0 ? -EYE_DX : EYE_DX) + s_eye_dx;
        int32_t dy = EYE_CY + s_eye_dy + s_lid_drop;
        lv_obj_set_width(s_eye[i], s_eye_w);
        lv_obj_set_height(s_eye[i], s_eye_h);
        lv_obj_set_style_radius(s_eye[i], s_eye_h / 2 + 1, LV_PART_MAIN);
        lv_obj_align(s_eye[i], LV_ALIGN_CENTER, dx, dy);
    }
}

/* Latch the resting height for the state, then draw. Blinks animate back to it. */
static void eyes_commit(void)
{
    s_eye_base = s_eye_h;
    s_lid_base = s_lid_drop;
    eyes_apply();
}

/* 2-arg lv_anim exec callbacks ONLY (never cast a 3-arg style setter). */
static void anim_eye_h_cb(void *var, int32_t v)
{
    (void)var;
    s_eye_h = v;
    /* a closing lid pulls the eye slightly down — reads as a real blink */
    s_lid_drop = s_lid_base + (s_eye_base - v) / 3;
    eyes_apply();
}

static void anim_glance_cb(void *var, int32_t v)
{
    (void)var;
    s_eye_dx = v;
    eyes_apply();
}

/* One anim, `times` repeats: a second pass is the natural "double blink".
 * (Two lv_anim_start calls with the same var+exec_cb would cancel each other.) */
static void do_blink(uint16_t times)
{
    lv_anim_t a;
    int32_t   close_h = (s_eye_base > 24) ? 6 : 4;

    if (s_eye_base <= 10) {
        return; /* already shut */
    }
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_eye[0]);
    lv_anim_set_exec_cb(&a, anim_eye_h_cb);
    lv_anim_set_values(&a, s_eye_base, close_h);
    lv_anim_set_time(&a, 80);
    lv_anim_set_playback_time(&a, 110);
    lv_anim_set_repeat_count(&a, times);
    lv_anim_set_repeat_delay(&a, 190);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
    lv_anim_start(&a);
}

static void do_glance(void)
{
    lv_anim_t a;
    int32_t   amt = (rnd(2) ? 1 : -1) * (EYE_W / 4);

    lv_anim_init(&a);
    lv_anim_set_var(&a, s_eye[1]);
    lv_anim_set_exec_cb(&a, anim_glance_cb);
    lv_anim_set_values(&a, 0, amt);
    lv_anim_set_time(&a, 420);
    lv_anim_set_playback_delay(&a, 700);
    lv_anim_set_playback_time(&a, 420);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
    lv_anim_start(&a);
}

static void blink_timer_cb(lv_timer_t *t)
{
    /* SLEEP keeps its eyes shut; ALERT stares. */
    if (s_state != APP_FACE_SLEEP && s_state != APP_FACE_ALERT) {
        int32_t r = rnd(100);
        do_blink(r < 22 ? 2 : 1);
        if (r > 78 && s_state == APP_FACE_IDLE) {
            do_glance();
        }
    }
    /* randomise the next blink so the cadence never feels metronomic */
    lv_timer_set_period(t, (uint32_t)(BLINK_MIN_MS + rnd(BLINK_VAR_MS)));
}

/***********************************************************
*********************** brows / cheeks *********************
***********************************************************/
/* dy_in / dy_out are 0..BROW_H; inner end = toward the nose. */
static void brows_show(int32_t dy_in, int32_t dy_out, int32_t lift)
{
    for (int i = 0; i < 2; i++) {
        int32_t y_outer = (i == 0) ? dy_out : dy_in;  /* left brow: x=0 is outer */
        int32_t y_inner = (i == 0) ? dy_in : dy_out;
        s_brow_pts[i][0].x = 0;
        s_brow_pts[i][0].y = y_outer;
        s_brow_pts[i][1].x = BROW_W;
        s_brow_pts[i][1].y = y_inner;
        lv_line_set_points(s_brow[i], s_brow_pts[i], 2);
        /* TOP_MID keeps the brow anchored by its top edge, so a flatter brow
         * does not jump when lv_line re-computes its self size. */
        lv_obj_align(s_brow[i], LV_ALIGN_TOP_MID,
                     (i == 0 ? -EYE_DX : EYE_DX),
                     PH / 2 + EYE_CY - EYE_H / 2 - BROW_H - lift);
        lv_obj_remove_flag(s_brow[i], LV_OBJ_FLAG_HIDDEN);
    }
}

static void brows_hide(void)
{
    lv_obj_add_flag(s_brow[0], LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_brow[1], LV_OBJ_FLAG_HIDDEN);
}

/***********************************************************
************************* mouth ****************************
***********************************************************/
/* Wide, shallow smile/frown built from a big-radius arc: the segment we show is
 * only the middle of a large circle, which is what gives BMO's gentle curve. */
static void mouth_curve(bool smile, int32_t half_span_deg, int32_t thick)
{
    lv_obj_remove_flag(s_smile, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_mouth_o, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_wave, LV_OBJ_FLAG_HIDDEN);

    if (half_span_deg < 12) half_span_deg = 12;
    if (half_span_deg > 60) half_span_deg = 60;
    lv_obj_set_style_arc_width(s_smile, thick, LV_PART_MAIN);

    if (smile) {
        /* bottom of the circle: ends curl upward -> smile */
        lv_arc_set_bg_angles(s_smile, 90 - half_span_deg, 90 + half_span_deg);
        lv_obj_align(s_smile, LV_ALIGN_CENTER, 0, MOUTH_CY - ARC_R);
    } else {
        /* top of the circle: ends curl downward -> frown */
        lv_arc_set_bg_angles(s_smile, 270 - half_span_deg, 270 + half_span_deg);
        lv_obj_align(s_smile, LV_ALIGN_CENTER, 0, MOUTH_CY + ARC_R);
    }
}

static void mouth_open(int32_t w, int32_t h)
{
    lv_obj_add_flag(s_smile, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_wave, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_mouth_o, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_size(s_mouth_o, w, h);
    lv_obj_set_style_radius(s_mouth_o, (h < w ? h : w) / 2, LV_PART_MAIN);
    lv_obj_align(s_mouth_o, LV_ALIGN_CENTER, 0, MOUTH_CY + h / 4);
}

static void mouth_wave(void)
{
    lv_obj_add_flag(s_smile, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_mouth_o, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_wave, LV_OBJ_FLAG_HIDDEN);
    lv_obj_align(s_wave, LV_ALIGN_TOP_MID, 0, PH / 2 + MOUTH_CY);
}

/* A short repeating pattern beats a plain toggle: it reads as syllables. */
static void talk_timer_cb(lv_timer_t *t)
{
    (void)t;
    static const int8_t pat[8] = {2, 6, 3, 8, 2, 5, 7, 3}; /* 1..8 openness */
    static uint8_t idx = 0;
    int32_t step = pat[idx++ & 0x07];
    int32_t h = (EYE_H / 6) + step * (EYE_H / 12);
    int32_t w = MOUTH_W - (8 - step) * (MOUTH_W / 40);
    mouth_open(w, h);
}

/***********************************************************
************************* alert ****************************
***********************************************************/
static void alert_timer_cb(lv_timer_t *t)
{
    (void)t;
    static bool on = false;
    on = !on;
    lv_obj_set_style_bg_color(s_plate, on ? COL_ALERT : COL_ALERT_DK, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_root, on ? COL_ALERT_DK : COL_ALERT, LV_PART_MAIN);
    /* a small shake sells the distress */
    lv_obj_align(s_plate, LV_ALIGN_CENTER, on ? 3 : -3, 0);
}

/***********************************************************
************************* sleep ****************************
***********************************************************/
static void z_place(int idx, int32_t v)
{
    lv_obj_align(s_z[idx], LV_ALIGN_TOP_MID,
                 EYE_DX + (int32_t)idx * (BROW_W / 3), Z_Y - v * (PH / 10) / 100);
    lv_opa_t o = (v < 45) ? LV_OPA_COVER : (lv_opa_t)(255 - ((v - 45) * 255) / 55);
    lv_obj_set_style_text_opa(s_z[idx], o, LV_PART_MAIN);
}

static void anim_z0_cb(void *var, int32_t v)
{
    (void)var;
    z_place(0, v);
}
static void anim_z1_cb(void *var, int32_t v)
{
    (void)var;
    z_place(1, v);
}

static void z_start(void)
{
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_z[0]);
    lv_anim_set_exec_cb(&a, anim_z0_cb);
    lv_anim_set_values(&a, 0, 100);
    lv_anim_set_time(&a, 2000);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_start(&a);

    lv_anim_init(&a);
    lv_anim_set_var(&a, s_z[1]);
    lv_anim_set_exec_cb(&a, anim_z1_cb);
    lv_anim_set_values(&a, 0, 100);
    lv_anim_set_time(&a, 2000);
    lv_anim_set_delay(&a, 900);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_start(&a);

    lv_obj_remove_flag(s_z[0], LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_z[1], LV_OBJ_FLAG_HIDDEN);
}

static void z_stop(void)
{
    lv_anim_delete(s_z[0], anim_z0_cb);
    lv_anim_delete(s_z[1], anim_z1_cb);
    lv_obj_add_flag(s_z[0], LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_z[1], LV_OBJ_FLAG_HIDDEN);
}

/***********************************************************
********************** build helpers ***********************
***********************************************************/
static void stop_timer(lv_timer_t **t)
{
    if (*t) {
        lv_timer_delete(*t);
        *t = NULL;
    }
}

static lv_obj_t *plain_box(lv_obj_t *parent, lv_color_t c)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_set_style_bg_color(o, c, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(o, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(o, 0, LV_PART_MAIN);
    lv_obj_set_style_shadow_width(o, 0, LV_PART_MAIN);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
    return o;
}

static void compute_geometry(int32_t w, int32_t h)
{
    if (w < 120) w = FALLBACK_W;
    if (h < 160) h = FALLBACK_H;
    if (w > 320) w = 320;
    if (h > 480) h = 480;

    PW = w - 12;
    PH = h - 12;

    EYE_W  = PW * 19 / 100;
    EYE_H  = EYE_W * 115 / 100;
    EYE_DX = PW * 22 / 100;
    EYE_CY = -(PH * 10 / 100);

    MOUTH_CY = PH * 16 / 100;
    MOUTH_W  = PW * 30 / 100;
    ARC_R    = PW * 62 / 100;

    BROW_W = EYE_W * 130 / 100;
    BROW_H = PH * 4 / 100;
    WAVE_W = PW * 34 / 100;

    Z_Y = PH * 20 / 100;

    s_eye_h = EYE_H;
    s_eye_w = EYE_W;
}

/***********************************************************
************************ public API ************************
***********************************************************/
OPERATE_RET app_face_init(lv_obj_t *parent)
{
    if (parent == NULL) {
        return OPRT_INVALID_PARM;
    }
    if (s_plate != NULL) {
        return OPRT_OK; /* already built */
    }

    s_root = parent;
    lv_obj_update_layout(parent);
    compute_geometry(lv_obj_get_content_width(parent), lv_obj_get_content_height(parent));

    lv_obj_set_style_bg_color(s_root, COL_BODY, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_root, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_root, 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_root, LV_OBJ_FLAG_SCROLLABLE);

    /* ---- the face screen (BMO's front panel) ---- */
    s_plate = plain_box(s_root, COL_PLATE);
    lv_obj_set_size(s_plate, PW, PH);
    lv_obj_set_style_radius(s_plate, PW / 12, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_plate, 4, LV_PART_MAIN);
    lv_obj_set_style_border_color(s_plate, COL_PLATE_EDG, LV_PART_MAIN);
    lv_obj_align(s_plate, LV_ALIGN_CENTER, 0, 0);

    /* ---- cheeks (behind the eyes in z-order: created first) ---- */
    for (int i = 0; i < 2; i++) {
        s_blush[i] = plain_box(s_plate, COL_BLUSH);
        lv_obj_set_size(s_blush[i], EYE_W * 3 / 4, EYE_W * 2 / 5);
        lv_obj_set_style_radius(s_blush[i], EYE_W / 5, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(s_blush[i], LV_OPA_60, LV_PART_MAIN);
        lv_obj_align(s_blush[i], LV_ALIGN_CENTER,
                     (i == 0 ? -1 : 1) * (EYE_DX + EYE_W / 3), EYE_CY + EYE_H * 3 / 4);
        lv_obj_add_flag(s_blush[i], LV_OBJ_FLAG_HIDDEN);
    }

    /* ---- eyes + glints ---- */
    for (int i = 0; i < 2; i++) {
        s_eye[i] = plain_box(s_plate, COL_INK);
        lv_obj_set_width(s_eye[i], EYE_W);
        lv_obj_set_height(s_eye[i], EYE_H);
        lv_obj_set_style_radius(s_eye[i], EYE_H / 2 + 1, LV_PART_MAIN);

        s_glint[i] = plain_box(s_eye[i], COL_GLINT);
        lv_obj_set_size(s_glint[i], EYE_W / 4, EYE_W / 4);
        lv_obj_set_style_radius(s_glint[i], EYE_W / 8, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(s_glint[i], LV_OPA_40, LV_PART_MAIN);
        lv_obj_align(s_glint[i], LV_ALIGN_TOP_LEFT, EYE_W / 2, EYE_W / 5);
    }
    eyes_commit();

    /* ---- brows (hidden unless a state wants them) ---- */
    for (int i = 0; i < 2; i++) {
        s_brow[i] = lv_line_create(s_plate);
        lv_obj_set_style_line_width(s_brow[i], EYE_W / 7 + 3, LV_PART_MAIN);
        lv_obj_set_style_line_color(s_brow[i], COL_INK, LV_PART_MAIN);
        lv_obj_set_style_line_rounded(s_brow[i], true, LV_PART_MAIN);
        lv_obj_add_flag(s_brow[i], LV_OBJ_FLAG_HIDDEN);
        s_brow_pts[i][0].x = 0;
        s_brow_pts[i][0].y = 0;
        s_brow_pts[i][1].x = BROW_W;
        s_brow_pts[i][1].y = BROW_H;
        lv_line_set_points(s_brow[i], s_brow_pts[i], 2);
    }

    /* ---- nose dot ---- */
    s_nose = plain_box(s_plate, COL_INK);
    lv_obj_set_size(s_nose, PW * 5 / 100, PW * 4 / 100);
    lv_obj_set_style_radius(s_nose, PW / 40, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_nose, LV_OPA_80, LV_PART_MAIN);
    lv_obj_align(s_nose, LV_ALIGN_CENTER, 0, EYE_CY + EYE_H / 2 + PH * 5 / 100);

    /* ---- smile / frown arc ---- */
    s_smile = lv_arc_create(s_plate);
    lv_obj_set_size(s_smile, ARC_R * 2, ARC_R * 2);
    lv_obj_remove_style(s_smile, NULL, LV_PART_KNOB);
    lv_obj_remove_style(s_smile, NULL, LV_PART_INDICATOR);
    lv_obj_remove_flag(s_smile, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(s_smile, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_opa(s_smile, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_width(s_smile, EYE_W / 5 + 3, LV_PART_MAIN);
    lv_obj_set_style_arc_color(s_smile, COL_INK, LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(s_smile, true, LV_PART_MAIN);

    /* ---- open mouth ---- */
    s_mouth_o = plain_box(s_plate, COL_INK);
    lv_obj_set_size(s_mouth_o, MOUTH_W, EYE_H / 2);
    lv_obj_add_flag(s_mouth_o, LV_OBJ_FLAG_HIDDEN);

    /* ---- worried wavy mouth ---- */
    s_wave = lv_line_create(s_plate);
    lv_obj_set_style_line_width(s_wave, EYE_W / 6 + 2, LV_PART_MAIN);
    lv_obj_set_style_line_color(s_wave, COL_INK, LV_PART_MAIN);
    lv_obj_set_style_line_rounded(s_wave, true, LV_PART_MAIN);
    for (int i = 0; i < 5; i++) {
        s_wave_pts[i].x = WAVE_W * i / 4;
        s_wave_pts[i].y = (i & 1) ? 0 : PH * 3 / 100;
    }
    lv_line_set_points(s_wave, s_wave_pts, 5);
    lv_obj_add_flag(s_wave, LV_OBJ_FLAG_HIDDEN);

    /* ---- sleepy z's ---- */
    for (int i = 0; i < 2; i++) {
        s_z[i] = lv_label_create(s_plate);
        lv_label_set_text(s_z[i], i == 0 ? "z" : "Z");
        lv_obj_set_style_text_color(s_z[i], COL_INK, LV_PART_MAIN);
        lv_obj_add_flag(s_z[i], LV_OBJ_FLAG_HIDDEN);
        lv_obj_align(s_z[i], LV_ALIGN_TOP_MID, EYE_DX + i * (BROW_W / 3), Z_Y);
    }

    s_blink_timer = lv_timer_create(blink_timer_cb, BLINK_MIN_MS, NULL);

    app_face_set_state(APP_FACE_IDLE);
    return OPRT_OK;
}

void app_face_set_state(APP_FACE_STATE_E state)
{
    if (s_plate == NULL) {
        return;
    }
    s_state = state;

    /* ---- reset every transient: each state re-arms only what it needs ---- */
    stop_timer(&s_talk_timer);
    stop_timer(&s_alert_timer);
    lv_anim_delete(s_eye[0], anim_eye_h_cb);
    lv_anim_delete(s_eye[1], anim_glance_cb);
    z_stop();
    brows_hide();
    lv_obj_add_flag(s_blush[0], LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_blush[1], LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_style_bg_color(s_plate, COL_PLATE, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_root, COL_BODY, LV_PART_MAIN);
    lv_obj_align(s_plate, LV_ALIGN_CENTER, 0, 0);
    lv_obj_remove_flag(s_nose, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_style_bg_opa(s_glint[0], LV_OPA_40, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_glint[1], LV_OPA_40, LV_PART_MAIN);

    s_eye_h    = EYE_H;
    s_eye_w    = EYE_W;
    s_eye_dx   = 0;
    s_eye_dy   = 0;
    s_lid_drop = 0;

    switch (state) {
    case APP_FACE_LISTENING:
        /* eyes wide and lifted — "I'm all ears" */
        s_eye_h  = EYE_H * 115 / 100;
        s_eye_w  = EYE_W * 106 / 100;
        s_eye_dy = -(EYE_H / 8);
        eyes_commit();
        mouth_curve(true, 30, EYE_W / 5 + 3);
        break;

    case APP_FACE_THINKING:
        /* squint + a slow side-to-side glance */
        s_eye_h  = EYE_H * 72 / 100;
        s_eye_dy = EYE_H / 12;
        eyes_commit();
        {
            lv_anim_t a;
            lv_anim_init(&a);
            lv_anim_set_var(&a, s_eye[1]);
            lv_anim_set_exec_cb(&a, anim_glance_cb);
            lv_anim_set_values(&a, -(EYE_W / 4), EYE_W / 4);
            lv_anim_set_time(&a, 900);
            lv_anim_set_playback_time(&a, 900);
            lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
            lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
            lv_anim_start(&a);
        }
        mouth_curve(true, 18, EYE_W / 6 + 2);
        break;

    case APP_FACE_SPEAKING:
        eyes_commit();
        mouth_open(MOUTH_W, EYE_H / 3);
        s_talk_timer = lv_timer_create(talk_timer_cb, TALK_MS, NULL);
        break;

    case APP_FACE_HAPPY:
        /* squished happy eyes, rosy cheeks, the widest smile */
        s_eye_h = EYE_H * 85 / 100;
        eyes_commit();
        mouth_curve(true, 44, EYE_W / 4 + 3);
        lv_obj_remove_flag(s_blush[0], LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_blush[1], LV_OBJ_FLAG_HIDDEN);
        break;

    case APP_FACE_SAD:
        /* droopy half-lidded eyes, inner brows up, frown */
        s_eye_h    = EYE_H * 68 / 100;
        s_eye_dy   = EYE_H / 6;
        s_lid_drop = EYE_H / 10;
        eyes_commit();
        brows_show(0, BROW_H, 0); /* inner end high, outer low */
        mouth_curve(false, 28, EYE_W / 5 + 3);
        break;

    case APP_FACE_SURPRISED:
        /* big eyes, raised brows, tiny round "o" mouth */
        s_eye_h  = EYE_H * 128 / 100;
        s_eye_w  = EYE_W * 114 / 100;
        s_eye_dy = -(EYE_H / 10);
        eyes_commit();
        brows_show(BROW_H / 2, BROW_H / 2, BROW_H / 2); /* flat, lifted */
        mouth_open(PW * 16 / 100, PW * 16 / 100);
        break;

    case APP_FACE_ALERT:
        /* red strobe, worried brows, wavy mouth, staring eyes */
        s_eye_h = EYE_H * 118 / 100;
        s_eye_w = EYE_W * 108 / 100;
        eyes_commit();
        brows_show(0, BROW_H, 0);
        mouth_wave();
        lv_obj_set_style_bg_color(s_plate, COL_ALERT, LV_PART_MAIN);
        lv_obj_set_style_bg_color(s_root, COL_ALERT_DK, LV_PART_MAIN);
        s_alert_timer = lv_timer_create(alert_timer_cb, 320, NULL);
        break;

    case APP_FACE_SLEEP:
        /* eyes shut to thin lines, gentle smile, drifting z's */
        s_eye_h    = 6;
        s_lid_drop = EYE_H / 3;
        eyes_commit();
        lv_obj_set_style_bg_opa(s_glint[0], LV_OPA_TRANSP, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(s_glint[1], LV_OPA_TRANSP, LV_PART_MAIN);
        mouth_curve(true, 20, EYE_W / 6 + 2);
        z_start();
        break;

    case APP_FACE_IDLE:
    default:
        eyes_commit();
        mouth_curve(true, 34, EYE_W / 5 + 3);
        break;
    }
}

void app_face_set_by_name(const char *name)
{
    if (name == NULL) {
        return;
    }
    /* Device status strings from the chat app (kept identical to v1). */
    if (0 == strcmp(name, "LISTENING")) { app_face_set_state(APP_FACE_LISTENING); return; }
    if (0 == strcmp(name, "SPEAKING"))  { app_face_set_state(APP_FACE_SPEAKING);  return; }
    if (0 == strcmp(name, "STANDBY"))   { app_face_set_state(APP_FACE_IDLE);      return; }

    /* Emotion names (substring match, so "very happy" etc. still work). */
    if (strstr(name, "happy") || strstr(name, "smile") || strstr(name, "laugh") ||
        strstr(name, "love")  || strstr(name, "excited"))                     { app_face_set_state(APP_FACE_HAPPY);     return; }
    if (strstr(name, "sad")   || strstr(name, "cry")   || strstr(name, "sorry")) { app_face_set_state(APP_FACE_SAD);    return; }
    if (strstr(name, "surprise") || strstr(name, "shock") || strstr(name, "wow")) { app_face_set_state(APP_FACE_SURPRISED); return; }
    if (strstr(name, "alert") || strstr(name, "alarm") || strstr(name, "scared") ||
        strstr(name, "help")  || strstr(name, "angry"))                       { app_face_set_state(APP_FACE_ALERT);     return; }
    if (strstr(name, "think") || strstr(name, "hmm")  || strstr(name, "wait")) { app_face_set_state(APP_FACE_THINKING);  return; }
    if (strstr(name, "sleep") || strstr(name, "tired") || strstr(name, "zzz")) { app_face_set_state(APP_FACE_SLEEP);     return; }

    app_face_set_state(APP_FACE_IDLE);
}
