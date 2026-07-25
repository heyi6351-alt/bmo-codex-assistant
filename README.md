# advx-bmo — BMO (AdventureX 2026)

A pocket **BMO** (Adventure Time). Two boards act as one device:
- **Tuya T5AI-Board** = BMO's face, voice, and touch screen (this repo's `app/`).
- **Orange Pi 3B** = BMO's always-on brain (runs the `armada` daemon + a trading API).
- **BMO PC Worker** = the permissioned PC capability executor (`bmo_worker/`):
  Codex website work, allowlisted file reads/search, system inspection, and
  fail-closed connector slots for browser, email, and desktop tools.

BMO covers **4 hackathon tracks**, each a "facet" (a screen + voice command) in the
Kaleidoscope shell:

| Track | Facet file | Owner | Status |
|---|---|---|---|
| **PANDA AI** (AI trading) | `app/src/app_trader.c` | **teammates** | working (shows live signals from the Pi) |
| **Photon** | `app/src/kaleido.c` → `s_facet_photon` (make `app/src/app_photon.c`) | **teammates** | placeholder — build it out |
| **StepFun** (AI agent drives your PC/browser) | `app/src/kaleido.c` → `s_facet_stepfun` + `pc_agent/` | core team | in progress |
| **LiberNovo** (always-on personal daemon) | `app/src/app_brain.c` (+ Armada on the Pi) | core team | working |

## Who edits what
- **Teammates: own `PANDA AI` and `Photon`.** Edit `app/src/app_trader.c` (PANDA AI) and
  build the `Photon` facet (start from the `s_facet_photon` placeholder in `kaleido.c`, or
  copy `app/src/app_arcade.c` into a new `app/src/app_photon.c` — it's the cleanest facet
  template — then register it in `kaleido.c`).
- Don't touch `app_brain.c` / `app_stepfun*` / `pc_agent/` (core team's StepFun + LiberNovo).

## How a facet works (the Kaleidoscope shell)
A facet is a `KALEIDO_APP_T` (see `app/src/kaleido.h`):
```c
KALEIDO_APP_T kaleido_app_x = {
    .name = "X", .track = "PANDA AI",
    .glyph = LV_SYMBOL_CHARGE, .tint = 0xF6C453,   // tint is a plain 0xRRGGBB uint32!
    .desc = "one friendly line",
    .build = __build,      // draws the facet UI into `root`
};
```
Register it in `kaleido.c`'s `kaleido_start()` with `kaleido_register(&kaleido_app_x)`.
Add a voice trigger in `kaleido_on_voice()`.

**Hard rules (LVGL 9, -Werror):**
- `.tint` must be a plain `0xRRGGBB` constant — never `lv_color_hex()` in a static init.
- Never touch LVGL objects from a worker thread. Do HTTP/work on a `tal_thread`, publish to
  the UI via a `static volatile` buffer + flag read by an `lv_timer` (copy the pattern in
  `app_trader.c` / `app_brain.c`).
- HTTP: use `http_client_request`/`http_client_free` like `app_trader.c` — never pre-alloc or
  free `response->buffer` (the library owns it).

## Build & flash
This is only the **app** — it plugs into the TuyaOpen SDK. Full steps are in
**`BMO_HANDOFF.md`** (architecture, gotchas) and **`KIMI_INSTRUCTIONS.md`** (ordered tasks).
Short version:
1. Get the TuyaOpen SDK (T5AI). Copy `app/` into `apps/tuya.ai/your_chat_bot` and
   `board/tuya_t5ai_ex_module.h` into `boards/T5AI/TUYA_T5AI_BOARD/`.
2. Build: `. .\export.ps1` → `cd apps/tuya.ai/your_chat_bot` → `tos.py build` (must say `Board: TUYA_T5AI_BOARD`).
3. Flash: `flash_fast.ps1` (**COM5**, not COM4).
4. Voice: press & **hold the KEY button**, speak, release.

## Config you must set (not in this repo — redacted)
- `app/include/tuya_config.h`: your Tuya **PID + UUID + AuthKey** (get from iot.tuya.com).
- `pc_agent/`: an `ANTHROPIC_API_KEY` in the environment (for the browser agent).
- The Orange Pi IP is hard-coded in `app_brain.c` / `app_trader.c` — update it to your Pi's IP.

See `BMO_HANDOFF.md` for the full picture (including the known Tuya-cloud/China-DC voice caveat).
