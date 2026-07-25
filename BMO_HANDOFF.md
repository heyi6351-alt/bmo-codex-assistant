# BMO — Project Handoff (Kaleidoscope)

> A pocket **BMO** (Adventure Time) built for AdventureX 2026. Two boards working as
> one device: a **Tuya T5AI-Board** is BMO's face/voice/screen, and an **Orange Pi 3B**
> is BMO's always-on **brain** (running the `armada` daemon). BMO's first feature is an
> **AI trading bot** (PANDA AI track). This document is the single source of truth for
> continuing the project — hand it to KIMI.

---

## 0. TL;DR status

| Piece | State |
|---|---|
| BMO firmware builds & flashes to the T5AI board | ✅ working |
| BMO face on screen (landscape, blinking, talk animation) | ✅ working |
| Voice/AI via Tuya cloud | ⚠️ **blocked** — license is in Tuya's **China** data center, unreachable over the VPN (see §6). Not a firmware bug. |
| Orange Pi + Armada brain | ✅ running, now reachable on the LAN (`10.68.9.201:8099`) |
| Tuya ↔ Orange Pi network link | ✅ proven (PC reached Armada API; boards on same subnet) |
| Tuya ↔ Orange Pi UART cable | ❌ no data — we use the network instead |
| BMO "Brain" facet (shows Armada on screen) | 🚧 being built (agent) |
| Trading bot on the Pi + API | 🚧 being built (agent) |
| Armada LLM brain enabled | 🚧 being wired (agent) |

---

## 1. Hardware

- **Tuya T5AI-Board** — module **T5-E1-IPEX** (BK7258, Cortex-M33 @480 MHz, 8 MB flash / 16 MB PSRAM), **3.5" 480×320 RGB565 touch LCD (ILI9488 + GT1151 touch)**, 2 mics + speaker, WiFi6 + BLE. Board silk: `T5-BOARD-35565LCD`.
  - USB shows as **two** CH342 serial ports: **COM4 = LOG/monitor**, **COM5 = FLASH/download**. (This matters — flashing only works on COM5.)
- **Orange Pi 3B** — RK3566 ARM64, 4 cores, 1.9 GB RAM, Ubuntu 22.04 (Jammy). Hostname `orangepi3b`, IP **10.68.9.201**, gateway 10.68.0.1.
- Both are on the **same WiFi subnet (10.68.9.x)**.

---

## 2. Repositories / locations

| Path | What |
|---|---|
| `D:\TuyaOpen-master1` | TuyaOpen SDK + BMO firmware. **git repo**; BMO work is commit `6629dea`. |
| `D:\TuyaOpen-master1\apps\tuya.ai\your_chat_bot` | **The BMO app** (our code lives in `src/`). |
| `D:\tradingaI.zip` | The MT5 trading bot (PANDA AI track first feature). |
| Orange Pi `~/` | `armada` daemon + (being added) `~/tradingai`. |
| Scratchpad `...\scratchpad\ssh_pi.py` | Helper to drive the Pi over SSH (password auth via paramiko). |

---

## 3. The BMO firmware (Tuya T5AI)

### 3.1 Architecture — "Kaleidoscope" shell
A small facet framework layered on top of TuyaOpen's `your_chat_bot` AI demo. The home
screen is **just BMO's animated face** (no buttons — navigation is by voice; touch is
only used inside games/facets, per the product decision).

| File (`apps/tuya.ai/your_chat_bot/src/`) | Role |
|---|---|
| `app_face.c` / `app_face.h` | **BMO's face** drawn with LVGL 9 primitives (no image assets). Pale-mint screen, two dark eyes with a white glint, and the open grin: black outline, white teeth band, teal tongue filling the lower mouth **edge-to-edge**. Blinks on a timer; the mouth opens/closes while `APP_FACE_SPEAKING`. State API: `app_face_set_state()` / `app_face_set_by_name("LISTENING"/"SPEAKING"/"happy"/…)`. |
| `kaleido.c` / `kaleido.h` | The shell: facet registry (`KALEIDO_APP_T`, `kaleido_register`, `kaleido_start`), screens (home/apps/about), and the **voice command matcher** `kaleido_on_voice()`. **Cross-thread UI rule:** worker threads set `static volatile` flags; an `lv_timer` (`__ui_poll_cb`) applies them on the LVGL thread. Never touch LVGL from another thread. |
| `app_chat_bot.c` | TuyaOpen chat glue. Calls `kaleido_start()` at end of init (under `ENABLE_COMP_AI_DISPLAY`); maps ASR/TTS events to the face (`SPEAKING`/`STANDBY`) and to `kaleido_on_voice()`. |
| `app_arcade.c` | Example facet (`kaleido_app_arcade`) — the template for new facets. |
| `app_guardian.c` | "Guardian" facet — listens for "help"/"I fell" and pushes an alert (ntfy). |
| `staging/*` | Not-yet-integrated facets: `app_oracle.c` (fintech), `app_daemon.c`, `app_courier.c` (ntfy), `app_deck.c` (**Orange Pi HTTP bridge stub** — base for the Brain facet), `app_face_v2.c`. |
| `include/tuya_config.h` | **PID + license** (`TUYA_PRODUCT_ID` fallback, `TUYA_OPENSDK_UUID`, `TUYA_OPENSDK_AUTHKEY`). |
| `app_default.config` | Board choice + **`CONFIG_TUYA_PRODUCT_ID`** (the real PID source) + LCD option. |

`KALEIDO_APP_T` fields: `name, track, desc, glyph, tint (uint32 0xRRGGBB), build/enter/exit/on_voice, screen`.
**Gotcha:** `tint` must be a plain hex constant — `lv_color_hex()` is *not* a constant
initializer; store `0xRRGGBB` and call `lv_color_hex(app->tint)` at use.

### 3.2 Board orientation
`boards/T5AI/TUYA_T5AI_BOARD/tuya_t5ai_ex_module.h` — the LCD is forced to **landscape**
via `BOARD_LCD_ROTATION = TUYA_DISPLAY_ROTATION_90` (LVGL software-rotates to 480×320).
`KAL_W 480 / KAL_H 320` in `kaleido.h`.

### 3.3 Board selection gotcha (already fixed, keep in mind)
The board is a Kconfig `choice` where `SPARKLEIOT_T5AI_DEV` is listed first, so it's the
**default** if nothing explicit is set. The Tuya preset only sets the LCD option, not the
board *choice*, so builds silently targeted the wrong board. Fixed by adding
`CONFIG_BOARD_CHOICE_TUYA_T5AI_BOARD=y` to `app_default.config`. Verify with the build
banner: it must say `Board: TUYA_T5AI_BOARD`.

### 3.4 BUILD
```powershell
cd D:\TuyaOpen-master1
$env:TUYAOPEN_CN_DOWNLOAD='1'
. .\export.ps1                       # activates uv/python/tos.py (re-run per new shell)
cd apps\tuya.ai\your_chat_bot
tos.py build                         # success => "BUILD SUCCESS" + Board: TUYA_T5AI_BOARD
```
Output image: `apps\tuya.ai\your_chat_bot\.build\bin\your_chat_bot_QIO_1.0.1.bin` (~4.2 MB).
If you change `app_default.config` (PID/board), delete `.build\cache\using.config` first so
it regenerates.

### 3.5 FLASH  (⚠️ COM5, not COM4)
Fast path (≈70 s) via `D:\TuyaOpen-master1\flash_fast.ps1` (tries 921600 then falls back):
```powershell
powershell -ExecutionPolicy Bypass -File D:\TuyaOpen-master1\flash_fast.ps1
```
It runs tyutool: `tyutool_cli.exe write -d t5 -f <QIO.bin> -p COM5 -b 921600`.
The board's auto-download circuit resets it into the bootloader — **no button presses
needed** on COM5. (COM4 gives "Handshake OK" then a write timeout — it's the wrong port.)
Watch logs: `tos.py monitor -p COM4` (460800 baud).

### 3.6 Known-good fixes already applied (don't re-break)
- `.tint` stored as `uint32` hex (not `lv_color_hex`) — constant-initializer rule.
- `mqtt_service.c` backoff arg changed `uint16_t`→`uint32_t` (SDK vs re-cloned `backoffAlgorithm`).
- Empty git commit so the T5-OS build's `grabRef.cmake` can resolve HEAD.
- `app_guardian.c` / `staging/app_courier.c`: removed a double-free (never pre-alloc/free
  `http_client_response_t.buffer` — the library owns it).

---

## 4. The Orange Pi brain (Armada)

`armada` = a personal always-on daemon (Go, v0.1.0). Installed at `/usr/local/bin/armada`,
run by `armada.service` (systemd).

- **API** (now LAN-exposed): `http://10.68.9.201:8099/v1/status | /v1/tasks | /v1/digest`.
- **CLI**: `armada do <goal>` (needs a brain), `armada add …`, `armada ls`, `armada digest`, `armada status`, `armada logs <id>`, `armada rm <id>`.
- **Config** (systemd unit env): `ARMADA_ADDR` (we set a drop-in →`0.0.0.0:8099`),
  `ARMADA_STATE_FILE=~/.armada/tasks.json`, `ARMADA_BRAIN` (currently `off` — being enabled by an agent).
- We added `/etc/systemd/system/armada.service.d/override.conf` to open it to the LAN.

### 4.1 Driving the Pi from this PC
No SSH key auth (it kept getting rejected); use the paramiko helper:
```bash
py "…\scratchpad\ssh_pi.py" "<remote bash command>"     # run a command
py "…\scratchpad\ssh_pi.py" --put <local> <remote>       # upload
py "…\scratchpad\ssh_pi.py" --get <remote> <local>       # download
```
Creds: `orangepi@10.68.9.201` / `orangepi`.

---

## 5. The two-board link

- **UART cable**: physically connected but **carries no data** — the Tuya firmware doesn't
  drive UART1 and the Pi's header UART isn't muxed. **Decision: use the network.**
- **Network (WiFi/HTTP)**: both on `10.68.9.x`; Armada answers on `:8099`. The Tuya "Brain"
  facet (being built) HTTP-GETs Armada and shows it on BMO's screen. This is the integration.

**Intended architecture**
```
  ┌─────────────── Tuya T5AI (BMO) ───────────────┐        ┌────────── Orange Pi 3B (brain) ──────────┐
  │ LVGL BMO face · mics · speaker · touch        │  WiFi  │ armada daemon  :8099  /v1/status|tasks|   │
  │ Kaleidoscope shell (facets, voice nav)        │◄──────►│   digest                                  │
  │ "Brain" facet → HTTP GET armada :8099         │  HTTP  │ trading-bot API :8100 /signal /analysis   │
  │ (voice/ASR+TTS via Tuya cloud — see §6)       │        │ (armada brain = LLM, orchestrates tasks)  │
  └───────────────────────────────────────────────┘        └───────────────────────────────────────────┘
```

---

## 6. ⚠️ The cloud / voice issue (skip for now — for KIMI to fix)

BMO's voice uses Tuya's cloud (ASR→LLM→TTS). It **connected and ran voice fine for ~8
minutes**, then dropped and couldn't reconnect. Root cause (confirmed by two deep dives):

- The license (`uuidXXXXXXXXXXXXXXXX`) + PID (`gv3guzkjwsyz9da9`) are provisioned in Tuya's
  **China data center** — the device only ever reaches `*.tuyacn.com` (region `AY`).
- The user is on a **VPN** (not physically in Korea); cross-border TCP to the China MQTT
  cluster is unstable → `tcp transporter connect failed -0x710a` (a TCP-connect timeout),
  then `mq ser cfg ack timeout`. Time/certs are fine; it's a **network-path** problem.
- **No firmware override exists** — the data center is baked into the license, not a setting.

**Fixes (pick one):**
1. **Durable:** on `iot.tuya.com`, create a new project/product/license under a **Western/Central-Europe data center**, drop the new UUID/AuthKey/PID into `include/tuya_config.h` + `app_default.config`, wipe device NVS, rebuild/flash, and pair with a same-region Smart Life account.
2. **Quick试:** give it a stable, low-latency link to the China DC (clean VPN egress, 2.4 GHz close to the AP) so the connection holds.
3. **Bypass the cloud entirely:** make the **Orange Pi the brain** (Armada + a Western LLM) and drive BMO's replies from there — see §7. This is the recommended long-term direction and sidesteps Tuya's cloud.

---

## 7. Tracks & the roadmap for KIMI

Two hackathon tracks (confirm exact requirements from the images
`Weixin Image_20260722225116…jpg` = **Armada track**, `…224336…jpg` = **PANDA AI track** —
they weren't saved to disk so verify the wording):

- **Armada track** → the Orange Pi's `armada` daemon *is* the entry: a personal, always-on
  AI brain that turns goals into running tasks. BMO is its face/voice.
- **PANDA AI track** → BMO's first feature: the **AI trading bot** doing (eventually) real
  trades, with AI-driven analysis, orchestrated by Armada.

### TODO for KIMI (priority order)
1. **Trading bot on the Pi** — the bot is MT5-based and **MT5 is Windows-only**; only the
   pure-Python analysis runs on ARM. Plan: run the live MT5 piece on a Windows host that
   **POSTs signals** to the Pi's trading API; run analysis on the Pi. (Agent set up the API +
   what runs where — see the appendix section that will be appended.)
2. **Armada brain** — enable `ARMADA_BRAIN` with a Western LLM key (agent documents the exact
   env/drop-in). Then `armada do "…"` and BMO requests both flow through it.
3. **BMO "Brain" facet** — finish/flash the firmware facet that shows Armada + the trading
   signal on BMO's screen (agent builds it; flash with §3.5).
4. **Voice** — apply a §6 fix (recommend the Western-DC re-license, or go fully local via Armada).
5. Integrate the remaining `staging/` facets (oracle/daemon/courier) as more BMO features.

---

## 8. Credentials & notes
- Tuya PID `gv3guzkjwsyz9da9`, license UUID `uuidXXXXXXXXXXXXXXXX` (China DC — see §6). A
  second license pair exists (`uuidYYYYYYYYYYYYYYYY`). **These are in `include/tuya_config.h`
  in the repo — treat as secret; rotate/replace when moving data centers.**
- `tradingaI.zip/.env` holds broker secrets — **never commit it, never run live trades in test.**
- Orange Pi: `orangepi@10.68.9.201` / `orangepi`.

*(Appendices with the trading-bot API, Armada-brain enable steps, and the Brain-facet code
are appended below as the agents finish.)*

---

## 9. Appendix — KIMI session (2026-07-25): Armada drives BMO + PANDA track live

### 9.1 Armada → BMO control channel (LiberNovo/Desktop-Daemon track) — ✅ WORKING on device
Armada (the brain) now **controls** the T5AI board (the body), not just answers polls.

- **Daemon side** (`C:\Users\asus\armada`, commit pending): new `internal/daemon/bmo.go`
  - `POST /v1/bmo {"action","arg"}` → queue a command (in-memory, cap 16, oldest dropped)
  - `GET /v1/bmo/next` → board polls this; pops oldest, **204 = nothing queued**
  - CLI: `armada bmo face happy` / `armada bmo say "text"` (see `cmd/armada/main.go` `runBmo`)
  - Cross-compile for the Pi: `GOOS=linux GOARCH=arm64 go build -o armada-linux-arm64 ./cmd/armada`
- **Firmware side** (`src/app_brain.c`): the brain worker also GETs `/v1/bmo/next` each 5 s cycle;
  commands staged via `volatile s_cmd_ready` and applied on the LVGL thread by a boot-time
  `lv_timer` (`__cmd_cb`). Actions: `face <state>` → `app_face_set_by_name(arg)`;
  `say <text>` → `kaleido_toast(arg)` + SPEAKING face;
  `open <trader|brain|apps|home>` → lock-free screen request (see below).
- **Boot-time link**: `brain_facet_start()` is called from `app_chat_bot.c` right after
  `kaleido_register(&kaleido_app_brain);` — the daemon drives the body even if nobody opens
  the Brain facet.
- **Verified 2026-07-25**: queued `face happy` + `say` from the PC; queue drained to 204 by
  the board (firmware flash_kimi2/3). Watch the screen to confirm pixels.
- **`open` action** (added same day): home has no touch nav (voice-only by design, and cloud
  voice is blocked §6), so the daemon picks what the body shows: `armada bmo open trader`.
  Firmware path: `__cmd_cb` → `kaleido_request_open/apps/home()` (new lock-free flags in
  `kaleido.c` `__ui_poll_cb`) — never `kaleido_open()` from an lv_timer (`lv_vendor_disp_lock`
  is non-recursive and already held → deadlock).

### 9.2 Trading API on the Pi (PANDA track) — ✅ RUNNING under Armada
- `~/tradingai/bmo_api.py` (Flask, 0.0.0.0:8100) runs as an **armada-managed service**:
  `armada add --name trading-api --kind service 'cd /home/orangepi/tradingai && ./.venv/bin/python bmo_api.py'`
- The venv had no pip — bootstrapped with `get-pip.py`, then `pip install -r requirements_arm.txt`.
- Endpoints: `/health` `/symbols` `/signal?symbol=XAUUSD&profile=aggressive&tf=15min` `/analysis`
  — verified from the PC (`curl http://10.68.9.201:8100/signal?...` → real JSON, direction
  `flat|long|short`, entry, indicators, reasons). Symbols: EURUSD GBPUSD GOLD US30 XAUUSD.
- Signal JSON quirks the firmware must tolerate: `direction` (not `signal`), `entry` (number;
  no `price` field), reason text under `reasons`/`summary` (array or string).

### 9.3 Trader facet (BMO shows the signal) — ✅ BUILT (app_trader.c)
- `src/app_trader.c`: `kaleido_app_trader` ("Trader", track "PandaAI", tint `0xF6C453`).
  Polls `:8100/signal?symbol=XAUUSD...` every 5 s; big direction label (green/red/neutral,
  montserrat_24 — 48 is NOT enabled in this board config), price, "why" panel.
  Registered in `app_chat_bot.c`; voice keywords in `kaleido.c`: trader/trading/trade/signal/oracle.

### 9.4 Armada brain — ✅ LIVE via local ollama (qwen2.5:0.5b)
- brain providers in `internal/brain`: `ollama` (default) | `anthropic` | `off`.
  `ARMADA_BRAIN` + `ARMADA_BRAIN_MODEL` env; anthropic needs `ANTHROPIC_API_KEY`.
- **Live config**: `/etc/systemd/system/armada.service.d/brain.conf` →
  `ARMADA_BRAIN=ollama`, `ARMADA_BRAIN_MODEL=qwen2.5:0.5b`. `armada status` shows
  `brain: ollama:qwen2.5:0.5b`; `armada do "<goal>"` plans+creates a task in ~22 s.
- **Why 0.5b**: `llama3.2:1b` OOM-wedged the 1.9 GB Pi TWICE (sshd/armada/flask all died;
  needed power-cycles). 0.5b leaves ~900 MB headroom. Its plans are mechanically correct
  (right runtime/body) but semantically weak (kind service vs scheduled slips) — for
  smarter plans set the anthropic provider with a real key, or use the factory's own env
  to point at Kimi (see §9.9).
- **Pi hardening (do not remove)**: 450 MB `/swapfile` (in fstab) + 957 MB zram;
  `/etc/systemd/system/ollama.service.d/limits.conf` → `MemoryMax=700M` (ollama can never
  wedge the box again); SD was 100% FULL (root cause of much instability) — models dir
  purged, keep an eye on `df -h /`.
- Downloads: huggingface.co + hf-mirror are unreachable FROM the Pi (campus net);
  ollama.com registry works (~400 KB/s, use a nohup retry loop — EOFs are frequent).
  PC↔Pi LAN bulk transfer is flaky; SFTP is broken; the reliable mover is
  `py -m http.server` on the PC + `curl` on the Pi (verify md5 both sides).

### 9.9 StepFun track — "self-evolving frontend factory" — ✅ BUILT into armada
`armada factory "<requirement>"` runs an unattended loop: plan → write index.html/
style.css/app.js → draw original generative-SVG assets → verify (mechanical ref check +
headless-browser screenshot if chrome/msedge exists + model critique; `ARMADA_FACTORY_VISION=1`
sends the actual PNG) → fix → up to 3 iterations, best kept. Artifacts in
`~/.armada/factory/<id>/` (`site/`, `factory.log`, `meta.json`); served live at
`http://<pi>:8099/v1/factory/<id>/site/`. API: `POST /v1/factory`, `GET /v1/factory/{id}`,
`.../log`, `.../site/`. Code: `internal/factory/*` + `internal/daemon/factory.go` (repo on the PC).
- LLM env (OpenAI-compatible): `ARMADA_FACTORY_BASE_URL` (default ollama `http://127.0.0.1:11434/v1`),
  `ARMADA_FACTORY_API_KEY`, `ARMADA_FACTORY_MODEL` (default qwen2.5:0.5b).
  **For real quality**: point at Kimi — base `https://api.kimi.com/coding/v1`, key from the
  Kimi Code Console, model `kimi-for-coding` (set in the armada.service drop-in).
- First live run on the Pi with the local 0.5b: id `6d803ee09b25` ("BMO landing page").

### 9.5 Ops notes for this network
- **SFTP to the Pi is broken** (ENOENT on write) and big SSH stdin transfers die. Move files by
  serving them on the PC (`py -m http.server PORT`) and `curl` from the Pi (PC = 10.68.9.197).
  Small exec commands via `ssh_pi.py` are fine. `put_pi.py` (raw-cat fallback) also fails — use HTTP.
- Flash from Git Bash: `powershell -NoProfile -ExecutionPolicy Bypass -File 'D:/TuyaOpen-master1/flash_fast.ps1'`
  (forward slashes — backslashes get eaten and PowerShell can't find the file).

### 9.6 Voice status — DEFERRED (decision recorded 2026-07-25)
The user is **not** buying a new Tuya license now, so cloud voice stays blocked (§6).
**Chosen direction: §6 option 3 — local voice via Armada** (BMO mic → STT → Armada brain on
the Pi → TTS back to BMO). This matches the LiberNovo local-first story: no cloud, the Pi
owns the pipeline, and BMO's face already has `LISTENING`/`SPEAKING` states to drive.
Re-licensing to a Western/Central-Europe data center (§6 option 1) remains the documented
alternative if Tuya cloud voice is ever wanted again.

### 9.7 Ops gotchas (learned from the trading-api work)
- **`armada rm <id>` does NOT kill the service process.** After removing a service, kill its
  PID manually (`pgrep -f <cmd>` then `kill <pid>`) or the port stays bound and the next
  `armada add` of the same service fails with "address already in use".
- **`armada add` over SSH hangs** — the spawned service inherits the SSH channel's stdout, so
  the channel never closes. Always background it with stdio detached:
  `nohup armada add --name X --kind service '<cmd>' > /tmp/add.log 2>&1 < /dev/null &`
  then verify with `armada ls` (and `cat /tmp/add.log` on failure).

### 9.8 Autostart audit (verified 2026-07-25)
- `systemctl is-enabled armada` → **enabled**; `systemctl is-active armada` → **active**.
- `armada ls` after boot → **trading-api** (service, running) + **heartbeat** (scheduled,
  idle). Armada respawns its registered services on boot — that IS the autostart story; no
  separate systemd unit is needed for the trading API.
- **Proves:** power on the Pi → `armada.service` starts → armada relaunches trading-api
  (`:8100/health` → `{"ok":true,...}`) → power on the T5AI → BMO's face boots on screen and
  the Brain facet's worker starts polling the Pi. No manual steps.
