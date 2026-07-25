/**
 * @file kaleido.h
 * @brief Kaleidoscope — a Flipper-Zero-style, BMO-faced multi-tool shell for the
 *        Tuya T5AI-Board (LVGL 9, 320x480 touch).
 *
 * One lovable device, many facets. A boot intro wakes BMO up, then a home screen
 * shows an animated face; tapping "apps" opens a grid of tiles, each a facet that
 * maps to a hackathon track (Companion/chat, Guardian, Oracle/finance, Daemon,
 * Courier, Arcade). Each facet is a KALEIDO_APP_T that owns one LVGL screen.
 *
 * Threading: LVGL runs in its own task. Public functions here that may be called
 * from other threads take the LVGL mutex internally; callbacks invoked from LVGL
 * context (tile clicks, timers) must use the __-prefixed lock-free variants.
 */
#ifndef __KALEIDO_H__
#define __KALEIDO_H__

#include "tuya_cloud_types.h"
#include "lvgl.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- screen geometry (TUYA_T5AI_BOARD 3.5" LCD, landscape) ----
 * The panel is rotated 90 deg (BOARD_LCD_ROTATION) so BMO's face is wide, like
 * the real BMO. LVGL reports the rotated resolution, so width > height here. */
#define KAL_W 480
#define KAL_H 320

/* ---- BMO theme ---- */
#define KAL_COL_BG      lv_color_hex(0x18564D) /* deep teal backdrop */
#define KAL_COL_FACE    lv_color_hex(0x25A99A) /* BMO teal face */
#define KAL_COL_INK     lv_color_hex(0x0B2E2A) /* eyes / mouth */
#define KAL_COL_ACCENT  lv_color_hex(0xF6C453) /* warm yellow */
#define KAL_COL_ALERT   lv_color_hex(0xD0342C) /* distress red */
#define KAL_COL_TEXT    lv_color_hex(0xF2FBF7)
#define KAL_COL_TILE    lv_color_hex(0x2FBFAE)

/* A facet = one app tile + one screen. */
typedef struct kaleido_app KALEIDO_APP_T;
struct kaleido_app {
    const char *name;  /* "Oracle" — shown on the tile */
    const char *track; /* "Fintech" — small sub-label, the track it targets */
    const char *desc;  /* one friendly line: what this facet does (for the About screen) */
    const char *glyph; /* short symbol/emoji text for the tile icon */
    uint32_t    tint;  /* tile color as 0xRRGGBB; convert with lv_color_hex() at use
                        * (lv_color_hex isn't a constant expr, so it can't sit in a
                        * static initializer) */

    /* build(): create the facet's UI into `root` (a fresh full screen). Called
     * once, lazily, the first time the facet is opened. LVGL context. */
    void (*build)(KALEIDO_APP_T *self, lv_obj_t *root);
    /* enter()/exit(): optional, each time the facet is shown/hidden. */
    void (*enter)(KALEIDO_APP_T *self);
    void (*exit)(KALEIDO_APP_T *self);
    /* on_voice(): optional — recognized speech while this facet is active. */
    void (*on_voice)(KALEIDO_APP_T *self, const char *text);

    lv_obj_t *screen; /* owned; NULL until first built (framework-managed) */
};

/* Register a facet before kaleido_start(). Up to KAL_MAX_APPS. */
#define KAL_MAX_APPS 8
void kaleido_register(KALEIDO_APP_T *app);

/* Boot the shell: play the intro, then show the BMO home. Call once, after the
 * ai_ui chat screen exists (e.g. end of app_chat_bot_init). Non-LVGL thread OK. */
void kaleido_start(void);

/* Navigation (take the LVGL mutex; safe from any thread). */
void kaleido_home(void);              /* BMO home screen */
void kaleido_open(KALEIDO_APP_T *app);/* open a facet */
void kaleido_open_apps(void);         /* the tile grid */
void kaleido_open_chat(void);         /* the existing ai_ui chat screen */
void kaleido_open_about(void);        /* "Who am I" — BMO introduces its features */

/* Lock-free screen requests (safe from lv_timer/worker context; applied by the
 * shell's own lv_timer). The daemon's "open" command drives these. */
void kaleido_request_open(KALEIDO_APP_T *app);
void kaleido_request_apps(void);
void kaleido_request_home(void);

/* Recognized speech in: routed to the guardian and the active facet. */
void kaleido_on_voice(const char *text);

/* Home-face expression passthrough (drives the BMO face on the home screen). */
void kaleido_face_set(const char *status_or_emotion); /* "LISTENING"/"happy"/... */
void kaleido_face_alert(void);        /* red alarm face + jump to home */

/* Small helpers for facet authors (call inside build(), LVGL context):
 * kaleido_header() adds a title bar + a back-to-apps button to the facet's
 * screen and returns a body container to fill. */
lv_obj_t *kaleido_header(lv_obj_t *root, const char *title);
void      kaleido_toast(const char *msg);

/* Facet instances (defined in their own files; registered in kaleido_start). */
extern KALEIDO_APP_T kaleido_app_arcade;
extern KALEIDO_APP_T kaleido_app_brain;
extern KALEIDO_APP_T kaleido_app_trader;

#ifdef __cplusplus
}
#endif

#endif /* __KALEIDO_H__ */
