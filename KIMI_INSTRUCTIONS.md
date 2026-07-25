# Instructions for KIMI — Finish the BMO device

You are taking over a working-in-progress hackathon project called **BMO**. Read
`D:\TuyaOpen-master1\BMO_HANDOFF.md` first (full architecture, file map, gotchas). This
file is your **ordered task list** with exact commands and acceptance criteria. Do the
tasks in order; each says how to know it's done.

## What BMO is
Two boards = one device. **Tuya T5AI-Board** = BMO's face/voice/touch screen. **Orange Pi
3B** = BMO's always-on brain (`armada` daemon). Two tracks:
- **Armada track** — the Orange Pi daemon that turns goals into running tasks.
- **PANDA AI track** — BMO's first feature: an AI trading bot (`D:\tradingaI.zip`) doing real trades.

## Golden rules (don't re-break these)
1. **Flash on COM5, monitor on COM4.** COM4 handshakes then fails to write. Auto-download works on COM5 (no buttons).
2. `KALEIDO_APP_T.tint` must be a plain `0xRRGGBB` uint32 — **never** `lv_color_hex()` in a static initializer.
3. **Never** touch LVGL objects from a worker thread. Do HTTP/work on a `tal_thread`, publish to UI via a `static volatile` buffer+flag read by an `lv_timer` (copy `kaleido.c`'s `__ui_poll_cb` pattern).
4. `http_client_response_t.buffer` is allocated **and freed** by the library — never pre-allocate or free it (double-free).
5. Board must build as `TUYA_T5AI_BOARD` (check the build banner). If you edit `app_default.config`, delete `apps\tuya.ai\your_chat_bot\.build\cache\using.config` before rebuilding.
6. `.env` in the trading bot holds broker secrets — never commit it, never run live trades while testing (paper/analysis only).

## Access & tools
- **Build** (PowerShell):
  ```powershell
  cd D:\TuyaOpen-master1; $env:TUYAOPEN_CN_DOWNLOAD='1'; . .\export.ps1
  cd apps\tuya.ai\your_chat_bot; tos.py build
  ```
  Success = `BUILD SUCCESS` and `Board: TUYA_T5AI_BOARD`.
- **Flash** (≈70 s): `powershell -ExecutionPolicy Bypass -File D:\TuyaOpen-master1\flash_fast.ps1`
- **Serial log**: `tos.py monitor -p COM4`
- **Drive the Orange Pi** (`orangepi@10.68.9.201` / `orangepi`):
  ```bash
  py "C:\Users\asus\AppData\Local\Temp\claude\C--Users-asus\218ff2f1-2ae6-4588-8e15-c8260da5981c\scratchpad\ssh_pi.py" "<remote cmd>"
  py "...\ssh_pi.py" --put <local> <remote>     # upload
  ```
  (If that scratchpad path is gone, recreate a 20-line paramiko helper — creds above. Or set up SSH keys: `ssh-copy-id` after fixing `chmod 755 ~` on the Pi so StrictModes accepts the key.)
- **Armada API**: `curl http://10.68.9.201:8099/v1/status` (also `/v1/tasks`, `/v1/digest`).

---

## TASK 1 — Verify the base still works
1. Build (command above) → expect `BUILD SUCCESS`, `Board: TUYA_T5AI_BOARD`.
2. Flash (`flash_fast.ps1`) → expect `Flash OK … SUCCESS @ 921600`.
3. Look at the board: landscape BMO face (mint screen, two eyes with glint, open grin), blinking.
**Done when:** BMO's face shows and the board is stable (watchdog logs on COM4, heap steady).

## TASK 2 — Finish & flash the BMO "Brain" facet (Tuya ↔ Orange Pi)
Goal: on BMO's screen, show the Orange Pi's Armada status live over WiFi. An agent created
`apps\tuya.ai\your_chat_bot\src\app_brain.c` and wired it in — verify/repair it:
1. Confirm `src\app_brain.c` exists and defines `KALEIDO_APP_T kaleido_app_brain` (name "Brain", track "OrangePi", tint `0x6FB3F2`). It must poll `http://10.68.9.201:8099/v1/status` + `/v1/digest` on a worker thread and show version/uptime/brain/tasks/digest via the lock-free `lv_timer` pattern.
2. Confirm it's registered in `app_chat_bot.c` (`kaleido_register(&kaleido_app_brain);` before `kaleido_start()`) and reachable by voice (`kaleido_on_voice()` matches "brain"/"daemon"/"orange").
3. `tos.py build` → fix any `-Werror` issues (unused static, `.tint` const, `lv_timer_delete`). Then flash.
4. On the board, trigger the Brain facet; from a PC run `curl http://10.68.9.201:8099/v1/status` and confirm the same numbers appear on BMO.
**Done when:** BMO displays live Armada status pulled over WiFi.
**If the Orange Pi IP changes** (DHCP): update the host in `app_brain.c` (or better, add mDNS/`orangepi3b.local`), rebuild, reflash.

## TASK 3 — Trading bot (PANDA AI track), BMO's first feature
The bot (`D:\tradingaI.zip`) is **MetaTrader5-based and MT5 is Windows-only**. Split it:
- **On the Orange Pi** (an agent set this up under `~/tradingai` with a venv + an HTTP API on
  `:8100`, e.g. `GET /signal`, `/analysis`): runs the pure-Python **analysis** (no MT5). Verify:
  `py "...\ssh_pi.py" "curl -s http://127.0.0.1:8100/signal"`.
- **On a Windows host**: run the live MT5 trading piece (`mt5_bot.py`) which needs the MT5
  terminal + broker login (in `.env`). Have it **POST executions/signals to the Pi API** so the
  Pi is the single source of truth BMO reads. Keep it in **paper mode** until fully verified.
Steps:
1. Confirm the Pi API responds (curl above). If the agent left it as an `armada add` task, `armada ls` shows it; else create a systemd unit.
2. Point BMO's Brain facet (or a new "Oracle"/"Trader" facet based on `staging/app_oracle.c`) at `http://10.68.9.201:8100/signal` so BMO shows the latest trade/signal.
3. Wire the MT5 live piece on Windows → Pi API. Only enable real orders after paper testing.
**Done when:** BMO shows a live trading signal that came from the bot, and (later) the MT5 piece executes from the analysis. **Do NOT enable real-money orders until the user explicitly approves.**

## TASK 4 — Give Armada a brain (LLM)
Armada's `ARMADA_BRAIN=off`. An agent documented the exact env/keys. To enable:
1. Read the agent's appended notes in `BMO_HANDOFF.md` (which LLM provider + env var Armada wants).
2. Add a systemd drop-in with the brain + the user's own API key (a Western LLM — OpenAI/Anthropic — reachable over the VPN, unlike Tuya's China cloud):
   ```bash
   py "...\ssh_pi.py" "sudo mkdir -p /etc/systemd/system/armada.service.d && printf '[Service]\nEnvironment=ARMADA_BRAIN=<provider>\nEnvironment=<KEY_ENV>=<USER_KEY>\n' | sudo tee /etc/systemd/system/armada.service.d/brain.conf && sudo systemctl daemon-reload && sudo systemctl restart armada && sleep 2 && armada status"
   ```
   (sudo password is `orangepi`; the helper's remote shell can `echo orangepi | sudo -S …` if needed.)
3. Test: `armada do "summarize today's trading signals"` → `armada ls` / `armada digest` show it ran.
**Done when:** `armada status` shows `brain:` not `none`, and `armada do "<goal>"` produces a task/result.

## TASK 5 — Voice (optional; the cloud is the blocker)
BMO's Tuya-cloud voice is stuck on the **China data center** (see HANDOFF §6). Choose:
- **A (recommended, durable):** on `iot.tuya.com` create a new project/product/license under a
  **Central-Europe** data center; put the new `UUID`/`AuthKey`/`PID` in `include\tuya_config.h`
  + `app_default.config`; delete `.build\cache\using.config`; rebuild; **erase device NVS**;
  reflash; pair with a same-region Smart Life account.
- **B (no Tuya cloud):** do voice **locally via Armada** — mic audio → (local or Western-cloud)
  STT → Armada brain → TTS back to BMO. Bigger build but fully under your control and matches the
  Armada track. BMO's face already has `SPEAKING`/`LISTENING` states to drive.
**Done when:** you speak to BMO and it answers (mouth animates on `SPEAKING`).

## TASK 6 — Demo polish
- BMO persona: paste the BMO character prompt (in the chat history / put it in the agent's
  system prompt) so replies are in-character.
- Make the Brain/Trader facet auto-open or show a small status on the home so the two-board
  story is visible without voice.
- Confirm both boards auto-start on power (Pi: `armada.service` + trading unit enabled; board: firmware boots to the face).

---

## Acceptance for "complete"
1. Power on both boards → BMO face on the T5AI, `armada` running on the Pi.
2. BMO shows **live Armada + trading data pulled over WiFi** (proves two-board integration).
3. `armada do "<goal>"` works (brain on).
4. Trading analysis runs on the Pi and (Windows piece) can execute trades in paper mode.
5. Voice answers (Task 5) — or a documented reason it's deferred.

Ask the user before: enabling **real-money trades**, or spending money on a new Tuya data-center
license. Everything else, proceed autonomously.
