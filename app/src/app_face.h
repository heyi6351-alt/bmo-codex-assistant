/**
 * @file app_face.h
 * @brief A BMO-style animated face for the T5AI screen (LVGL 9).
 *
 * A vector-drawn, expressive face — two blinking eyes and a mouth that smiles,
 * frowns, and "talks" — inspired by BMO from Adventure Time. It draws with LVGL
 * primitives only (no image assets to flash), so it builds and runs as-is and is
 * easy to tune. Drive it with app_face_set_state() from the display task.
 */
#ifndef __APP_FACE_H__
#define __APP_FACE_H__

#include "tuya_cloud_types.h"
#include "lvgl.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    APP_FACE_IDLE = 0,  // calm, slow blink
    APP_FACE_LISTENING, // eyes wide, looking up — "I'm listening"
    APP_FACE_THINKING,  // eyes to the side
    APP_FACE_SPEAKING,  // mouth animates open/close
    APP_FACE_HAPPY,     // big smile
    APP_FACE_SAD,       // frown
    APP_FACE_SURPRISED, // wide eyes, open mouth
    APP_FACE_ALERT,     // red flashing — distress
    APP_FACE_SLEEP,     // eyes closed
} APP_FACE_STATE_E;

/**
 * @brief Build the animated face into `parent` (a container/screen you own).
 *        Call once, from LVGL context / under the LVGL mutex. The face fills
 *        `parent`; it no longer creates or loads its own screen.
 */
OPERATE_RET app_face_init(lv_obj_t *parent);

/**
 * @brief Change the expression / animation. Call under the LVGL mutex.
 */
void app_face_set_state(APP_FACE_STATE_E state);

/**
 * @brief Map a status string ("STANDBY"/"LISTENING"/"SPEAKING") or an emotion
 *        name ("happy"/"sad"/"neutral"/...) to a face state. Convenience for
 *        wiring into the existing display message handler.
 */
void app_face_set_by_name(const char *name);

#ifdef __cplusplus
}
#endif

#endif /* __APP_FACE_H__ */
