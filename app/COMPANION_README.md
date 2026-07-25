# Companion — T5AI-Board (built on `your_chat_bot`)

A voice AI companion with a **BMO-style animated face** and a **Guardian** that
listens for distress ("help", "I fell", "call my son") and, in seconds, flashes
a red alarm face, sounds a tone, and **pushes a notification to a phone**. One
person at home; someone who cares is alerted instantly.

## What was added (all wired in already)
| File | Role |
|---|---|
| `src/app_face.c` / `.h` | BMO-style face in LVGL 8 — blinking eyes, smile/frown, talking mouth, red ALERT. Vector-drawn, no image assets to flash. |
| `src/app_guardian.c` / `.h` | Distress-phrase detection → red face + alarm tone + phone push via ntfy.sh. |
| `src/display2/app_display.c` | Hooked: builds the face on boot; drives it from status/emotion; feeds recognized speech to the guardian. |

## ⚙️ One line to edit before flashing
In `src/app_guardian.c`, set your push topic:
```c
#define GUARDIAN_NTFY_TOPIC "companion-sos-change-me"   // make it unique, e.g. companion-sos-8fk3q
```
Then on a phone: install the free **ntfy** app (App Store / Play Store / F-Droid),
tap **+**, subscribe to the **same** topic string. That's your alert receiver.
No account, no server.

## Build + flash (one command)
1. Put the uv zip at `D:\TuyaOpen-master\.tools\archives\uv\0.11.18\uv-x86_64-pc-windows-msvc.zip`.
2. From `D:\TuyaOpen-master`: `.\go.ps1`
3. When the board menu appears, pick **T5AI → TUYA_T5AI_BOARD**.
4. It builds, flashes to COM4, and opens the serial log.

## Demo (what the judges see)
1. Board boots → BMO face blinks (idle).
2. Talk to it → face "listens" (eyes up) and "talks" (mouth moves) as it replies.
3. Say **"help, I fell"** → face turns **red and flashes**, a tone plays, and a
   phone across the room **buzzes with an SOS push** — live, in seconds.

## Tuning
- Face colors / eye positions: `#define`s at the top of `app_face.c`.
- Distress phrases (add your language): `k_distress[]` in `app_guardian.c`.
- Re-alert cooldown: `GUARDIAN_DEBOUNCE_MS` (default 30s).

## If flashing fails
- Change `$Port = 'COM4'` to `'COM5'` at the top of `go.ps1` (the board has two
  UARTs — one flashes, one logs).
- Unplug/replug; some boards need BOOT held + RST tapped to enter flash mode.
