/**
 * @file app_arcade.c
 * @brief Arcade facet — "Bug Squash", a touch reflex game. Fully offline (no
 *        Wi-Fi, no cloud, no license), so the device is fun the instant it boots.
 *        Tap the bug before the timer runs out; each hit scores and it jumps.
 */
#include "kaleido.h"
#include "tal_api.h"
#include <stdlib.h>

#define GAME_SECONDS 30

static lv_obj_t   *s_bug, *s_score_lbl, *s_time_lbl, *s_start_btn, *s_start_lbl;
static lv_timer_t *s_tick;
static int         s_score, s_time_left, s_running, s_seeded;

static void __move_bug(void)
{
    int x = 6 + (rand() % (KAL_W - 80));
    int y = 60 + (rand() % (KAL_H - 44 - 170));
    lv_obj_set_pos(s_bug, x, y);
}

static void __tick_cb(lv_timer_t *t)
{
    (void)t;
    if (!s_running) {
        return;
    }
    s_time_left--;
    lv_label_set_text_fmt(s_time_lbl, "Time %d", s_time_left);
    if (s_time_left <= 0) {
        s_running = 0;
        lv_obj_add_flag(s_bug, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text_fmt(s_start_lbl, "Score %d " LV_SYMBOL_REFRESH, s_score);
        lv_obj_clear_flag(s_start_btn, LV_OBJ_FLAG_HIDDEN);
    }
}

static void __bug_cb(lv_event_t *e)
{
    (void)e;
    if (!s_running) {
        return;
    }
    s_score++;
    lv_label_set_text_fmt(s_score_lbl, "Score %d", s_score);
    __move_bug();
}

static void __start_cb(lv_event_t *e)
{
    (void)e;
    if (!s_seeded) {
        srand((unsigned)tal_system_get_millisecond());
        s_seeded = 1;
    }
    s_score = 0;
    s_time_left = GAME_SECONDS;
    s_running = 1;
    lv_label_set_text(s_score_lbl, "Score 0");
    lv_label_set_text_fmt(s_time_lbl, "Time %d", GAME_SECONDS);
    lv_obj_add_flag(s_start_btn, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(s_bug, LV_OBJ_FLAG_HIDDEN);
    __move_bug();
}

static void __build(KALEIDO_APP_T *self, lv_obj_t *root)
{
    (void)self;
    lv_obj_t *body = kaleido_header(root, "Arcade");

    s_score_lbl = lv_label_create(body);
    lv_label_set_text(s_score_lbl, "Score 0");
    lv_obj_set_style_text_color(s_score_lbl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(s_score_lbl, LV_ALIGN_TOP_LEFT, 0, 0);

    s_time_lbl = lv_label_create(body);
    lv_label_set_text_fmt(s_time_lbl, "Time %d", GAME_SECONDS);
    lv_obj_set_style_text_color(s_time_lbl, KAL_COL_TEXT, LV_PART_MAIN);
    lv_obj_align(s_time_lbl, LV_ALIGN_TOP_RIGHT, 0, 0);

    /* the bug */
    s_bug = lv_obj_create(body);
    lv_obj_set_size(s_bug, 62, 62);
    lv_obj_set_style_radius(s_bug, 31, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_bug, KAL_COL_ALERT, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_bug, 3, LV_PART_MAIN);
    lv_obj_set_style_border_color(s_bug, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_remove_flag(s_bug, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(s_bug, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(s_bug, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(s_bug, __bug_cb, LV_EVENT_CLICKED, NULL);

    /* start / play-again button */
    s_start_btn = lv_obj_create(body);
    lv_obj_set_size(s_start_btn, 210, 58);
    lv_obj_align(s_start_btn, LV_ALIGN_CENTER, 0, 60);
    lv_obj_set_style_bg_color(s_start_btn, KAL_COL_ACCENT, LV_PART_MAIN);
    lv_obj_set_style_radius(s_start_btn, 14, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_start_btn, 0, LV_PART_MAIN);
    lv_obj_remove_flag(s_start_btn, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(s_start_btn, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_start_btn, __start_cb, LV_EVENT_CLICKED, NULL);
    s_start_lbl = lv_label_create(s_start_btn);
    lv_label_set_text(s_start_lbl, "TAP TO START");
    lv_obj_set_style_text_color(s_start_lbl, KAL_COL_INK, LV_PART_MAIN);
    lv_obj_center(s_start_lbl);

    s_tick = lv_timer_create(__tick_cb, 1000, NULL);
}

KALEIDO_APP_T kaleido_app_arcade = {
    .name  = "Arcade",
    .track = "Play",
    .desc  = "Let's play! Tap the bug before time runs out.",
    .glyph = LV_SYMBOL_PLAY,
    .tint  = 0xF6A5C0,
    .build = __build,
};
