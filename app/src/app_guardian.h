/**
 * @file app_guardian.h
 * @brief Guardian: turns the chat toy into a companion that watches over you.
 *
 * It listens to what you say (the already-transcribed text the app shows on
 * screen). If it hears distress — "help", "I fell", "call my son", "I can't
 * breathe" — it instantly: flips the face to a red ALERT, plays an alarm tone,
 * and pushes a notification to a phone via ntfy.sh (a free, no-account push
 * service). One person at home; someone who cares gets pinged in seconds.
 */
#ifndef __APP_GUARDIAN_H__
#define __APP_GUARDIAN_H__

#include "tuya_cloud_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Start the guardian (creates its alert worker). Call once at boot. */
OPERATE_RET app_guardian_init(void);

/**
 * @brief Feed a line of recognized user speech. If it contains a distress
 *        phrase, the guardian raises the alarm. Safe to call from the display
 *        task (it does no blocking network work itself).
 */
void app_guardian_on_text(const char *text);

#ifdef __cplusplus
}
#endif

#endif /* __APP_GUARDIAN_H__ */
