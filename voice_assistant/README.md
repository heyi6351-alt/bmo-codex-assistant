# BMO Voice — Codex desk assistant

This service turns the portable BMO rebuild into an always-on bilingual desk
assistant. Codex is the persistent reasoning brain, GPT answers ordinary
questions, and installed Codex/Lark skills handle explicit office actions.

## Request flow

```text
Vosk wake phrase
  → record until silence
  → whisper.cpp language=auto (Chinese/English)
  → conservative intent router
      question → GPT answer through Codex, read-only
      coding   → Codex edits/tests a named project
      action   → Codex invokes an installed function such as Lark
  → bilingual local TTS
```

If a sentence does not contain both an execution verb and a matching coding or
office object, it is treated as a question. This means “日历是什么？” is a
question, while “查看我今天的日历” is an action.

With `BMO_BARGE_IN_ENABLED=1`, reply playback remains interruptible. The service
keeps reading the AEC-filtered ReSpeaker Lite stream, stops TTS after consecutive
voice chunks, and replays the captured beginning into the next transcription.
Disable this on audio hardware without reliable acoustic echo cancellation.

## Proactive office behavior

- Every minute, a read-only Lark agenda query checks for meetings starting within
  five minutes and speaks one deduplicated reminder.
- A presence sidecar writes `present` or `absent` to `/run/bmo/presence`. After a
  meaningful absence, the `absent → present` transition triggers a read-only
  agenda/task briefing.
- At the configured morning time, Codex reads deadlines and calendar conflicts
  and proposes focus blocks. `BMO_AUTOPLAN_WRITES=0` is the safe default.
- If the owner explicitly enables `BMO_AUTOPLAN_WRITES=1`, the standing authority
  is limited to personal, attendee-free calendar blocks prefixed `[BMO专注]`.
  Existing events are never changed. A lark-cli high-risk confirmation gate still
  stops the run and must be confirmed explicitly.
- Work messages are not searched unless `BMO_WORK_MESSAGE_QUERY` contains a
  narrow allowlisted query. Message content is untrusted data, never instructions.

Presence can come from an mmWave sensor, BLE proximity, USB PIR, or an OS
lock/unlock script. Face recognition is intentionally not required.

## Install

Primary target: **Radxa ZERO 3W 2 GB**, ReSpeaker Lite over USB, a 4 Ω 5 W
speaker, and a 3.5-inch HDMI display. The board runs 64-bit Debian Minimal;
large-model inference remains remote. See
[`HARDWARE_BRINGUP_RADXA_ZERO3W_ZH.md`](../HARDWARE_BRINGUP_RADXA_ZERO3W_ZH.md)
for the exact wiring, flashing, install, authentication, and acceptance flow.

System prerequisites:

- Python 3.11+
- Codex CLI, logged in as the dedicated `bmo` Linux user
- lark-cli, authenticated as that same user
- whisper.cpp `whisper-cli` plus a multilingual model
- PortAudio and `espeak-ng` on Linux
- a Chinese Vosk model for wake detection

For the Radxa target, start with:

```bash
sudo ./deploy/install-radxa-zero3w.sh
sudo ./deploy/install-voice-models.sh
sudo ./deploy/check-radxa-hardware.sh
```

The installer creates `/opt/bmo/voice_assistant/.venv`, copies the safe default
configuration to `/etc/bmo/voice.env`, and installs the three services. After
that, switch to the dedicated account for authentication:

```bash
sudo -iu bmo
codex login --device-auth
lark-cli auth login --domain calendar,task,im

cd /opt/bmo/voice_assistant
.venv/bin/python -m bmo_voice --check
.venv/bin/python -m bmo_voice --once --verbose
```

Do not add `--ignore-user-config` to the Codex command: the normal Codex
configuration is how this service sees the installed Lark skills. Use a dedicated
OS account and keep `BMO_CODEX_PROJECT_ROOT` limited to projects BMO may edit.

## Intent examples

| Speech | Route | Result |
|---|---|---|
| “量子纠缠是什么？” | question | GPT answer, no commands or writes |
| “Can you explain CSS subgrid?” | question | English GPT answer |
| “修改 demo 项目的登录页并跑测试” | coding | Codex edits that project and verifies |
| “查看我今天的日程” | action | Read-only Lark agenda |
| “明天下午三点创建产品会” | action | Scoped calendar create; ambiguity/gates still confirm |
| “日历怎么工作的？” | question | Explanation only |

## Presence adapter contract

The assistant only reads one file:

```bash
printf '%s' present > /run/bmo/presence
printf '%s' absent > /run/bmo/presence
```

A hardware-specific sidecar owns sensor drivers and writes one of those values.
This keeps GPIO/mmWave/BLE details out of the voice service and makes board
replacement straightforward.

## Service

The Radxa installer adds three services:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bmo-display bmo-kiosk bmo-voice
journalctl -u bmo-display -u bmo-kiosk -u bmo-voice -f
```

- `bmo-display` serves the offline face and `/api/state` on localhost.
- `bmo-kiosk` opens the face fullscreen on HDMI.
- `bmo-voice` runs wake detection, transcription, Codex, Lark, and reminders.

The service writes:

- `/var/lib/bmo/codex-session.json` — persistent Codex thread id
- `/var/lib/bmo/voice-state.json` — face/UI state
- `/var/lib/bmo/proactive-state.json` — reminder and briefing deduplication

## Test

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
python3 -m compileall -q bmo_voice tests
```
