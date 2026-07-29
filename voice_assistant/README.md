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
      coding   → background Codex job inside the project whitelist
      action   → Codex invokes an installed function such as Lark
  → bilingual local TTS
```

If a sentence does not contain both an execution verb and a matching coding or
office object, it is treated as a question. This means “日历是什么？” is a
question, while “查看我今天的日历” is an action. Ordinary questions always run
in Codex's `read-only` sandbox, which prevents file writes; the question policy
also explicitly forbids command and tool use.

Utterances containing high-risk terms (delete, publish, deploy, send message,
payment, …) — whether action or coding — pause for an explicit spoken
「确认」. Negatives win over affirmatives (“好的，那算了” cancels), and a
pending confirmation expires after `BMO_CONFIRMATION_TIMEOUT_SECONDS`
(default 120 s) instead of staying armed forever.

## Brain provider (sparing Codex quota)

`BMO_BRAIN` selects the reasoning brain for spoken turns:

- `codex` (default) — Codex CLI handles every turn.
- `openai` — an OpenAI-compatible chat endpoint answers ordinary **questions**
  and drafts **proactive briefings**, so casual Q&A does not consume Codex
  quota. **Coding jobs and Lark actions still use Codex** (they need agentic
  tool execution a plain text model cannot do).

Set the endpoint (key comes from the environment, never source):

```bash
BMO_BRAIN=openai
BMO_OPENAI_BASE_URL=https://your-endpoint/v1   # has /chat/completions under it
BMO_OPENAI_MODEL=your-model
BMO_OPENAI_API_KEY=...                          # in /etc/bmo/voice.env, not code
```

If your Codex quota itself is exhausted, also point Codex at the same
OpenAI-compatible endpoint so coding/actions stop using that quota: define a
model provider in `~/.codex/config.toml` (base_url + env key) as the `bmo` user
and set `BMO_CODEX_PROFILE` / `BMO_CODEX_MODEL`. Codex needs a model with
tool/function calling for the coding path.

## Background coding jobs

Coding requests never block the conversation. `CodexJobManager` accepts one job
at a time, returns a job id immediately, and runs `codex exec` on a worker
thread with `start_new_session=True`:

- Say “进度 / 做到哪了” for a status summary, “取消当前任务” to cancel, and
  “重试” to resubmit the last failed/cancelled/interrupted job.
- Cancel and timeout stop the whole process group (SIGTERM, then SIGKILL), so
  Codex's own child processes cannot survive.
- Jobs persist to `/var/lib/bmo/codex-jobs.json`. Jobs that were active when
  the service stopped come back as `interrupted` and can be retried.
- Project targeting is limited to direct subdirectories of
  `BMO_CODEX_PROJECT_ROOT`; symlinks escaping the root are rejected.
- Completion, failure, and cancellation are spoken by the main loop between
  wake-poll windows, so voice stays responsive while a job runs.

## Confirmation-gated GitHub delivery

Every coding job starts from a clean Git repository and creates a local
`bmo/<job-id>` branch before Codex edits anything. Completion does **not**
commit, push, or contact GitHub. BMO records the exact changed paths, speaks the
summary, and waits for a fresh spoken “确认发布” bound to that same job.

After confirmation, BMO re-checks the branch, base commit, changed-path set,
GitHub CLI login, and confirmation expiry. It then stages only the recorded
paths, creates one commit, pushes the explicit BMO branch ref, and creates or
reuses an open Pull Request. It never invokes merge, never pushes `main` or
`master`, and never auto-resumes a half-finished publication after reboot.

- Say “不发布” to keep the branch and files locally without any remote write.
- Say “重新发布” after a failed/declined attempt to obtain a new confirmation
  window; this does not itself publish.
- The HDMI job card shows the resulting PR URL. Open that URL from another
  computer to inspect the diff, test summary, and continue the normal team
  review process.

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
- Proactive speech is suppressed when any sidecar writes `1`/`true`/`dnd` to
  `BMO_DND_FILE`, during the `BMO_QUIET_HOURS_START`–`BMO_QUIET_HOURS_END`
  window (overnight ranges supported), after “今天别提醒我”, or during a
  “推迟 N 分钟” snooze. “恢复提醒” clears the daily suppression and snooze.

Presence can come from an mmWave sensor, BLE proximity, USB PIR, or an OS
lock/unlock script. Face recognition is intentionally not required.

## Install

Primary target: **Orange Pi Zero 3 2 GB**, ReSpeaker Lite over USB, a 4 Ω 5 W
speaker, and a 4.3-inch 800×480 non-touch HDMI display. The board runs the
official 64-bit Debian 12 Bookworm Server image; large-model inference remains
remote. See
[`HARDWARE_BRINGUP_ORANGEPI_ZERO3_ZH.md`](../HARDWARE_BRINGUP_ORANGEPI_ZERO3_ZH.md)
for the exact wiring, flashing, install, authentication, and acceptance flow.

System prerequisites:

- Python 3.11+
- Codex CLI, logged in as the dedicated `bmo` Linux user
- Git and GitHub CLI (`gh`), with `gh auth login` completed as `bmo`
- lark-cli, authenticated as that same user
- whisper.cpp `whisper-cli` plus a multilingual model
- PortAudio and `espeak-ng` on Linux
- a Chinese Vosk model for wake detection

For the Orange Pi target, start with:

```bash
sudo ./deploy/install-orangepi-zero3.sh
sudo ./deploy/install-voice-models.sh
```

The installer creates `/opt/bmo/voice_assistant/.venv`, copies the safe default
configuration to `/etc/bmo/voice.env`, and installs the three services. After
that, switch to the dedicated account for authentication and pre-flight, then
start the services and run the hardware self-check last:

```bash
sudo -iu bmo
codex login --device-auth
gh auth login
gh auth status
lark-cli auth login --domain calendar,task,im

cd /opt/bmo/voice_assistant
set -a
. /etc/bmo/voice.env
set +a
.venv/bin/python -m bmo_voice --check
exit

sudo systemctl enable --now bmo-display bmo-kiosk bmo-voice
sudo ./deploy/check-orangepi-zero3-hardware.sh
```

`--check` needs the runtime environment, so source `/etc/bmo/voice.env` first
(otherwise it exits with `BMO_VOSK_MODEL must point to ...`). The hardware
self-check is run last: it treats board/audio/display items as pass/fail and
Codex/GitHub/Lark authorization as informational notes.

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
| “发送邮件给张三” | action | High-risk: waits for spoken 「确认」 within 120 s |
| “删除 demo 项目的测试文件” | coding | High-risk: waits for 「确认」, then runs as a background job |
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

The Orange Pi installer adds three services:

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
- `/var/lib/bmo/codex-jobs.json` — background coding job history and recovery
- `/var/lib/bmo/voice-state.json` — face/UI state
- `/var/lib/bmo/proactive-state.json` — reminder and briefing deduplication

## Display

The offline face page (`web/`) fits the 800×480 HDMI panel without scrolling:
a header with the intent pill and connection dot, the animated face, a job card
(id / project / state / PR URL) shown during coding and publication, a two-line
subtitle, and the footer status/clock. Subtitles are clamped to two lines and
long job fields are ellipsized, so nothing is cropped mid-glyph or scrolls.

## Status

All software above is implemented and covered by unit tests (intent routing,
confirmations, job lifecycle, cancellation races, restart recovery, safe PR
publication, proactive suppression, display API). Publication tests use an
injected command runner and never access a real remote. Still pending
real-hardware acceptance on the Orange Pi Zero 3 + ReSpeaker Lite + 800×480
panel: wake/barge-in thresholds, actual Codex and GitHub runs end to end, TTS
audibility, and on-panel visual checks.

## Test

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
python3 -m compileall -q bmo_voice tests
```
