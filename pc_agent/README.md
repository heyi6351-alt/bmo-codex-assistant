# BMO PC Agent — "BMO controls the PC"

A small HTTP service that lets **BMO** (via its Orange Pi **Armada** brain) drive a
real **Chromium** browser on **this Windows PC** to look things up on the public web
and report an answer back — like the [`browser-use`](https://github.com/browser-use/browser-use)
library.

## What it does

You give it a goal (`"find the current time in Seoul"`). It:

1. Launches a real Chromium browser (headless) via **Playwright**.
2. Runs an agentic loop with **Claude** (Anthropic SDK). Claude is given a small set
   of browser tools — `web_search`, `open_url`, `read_page`, `find_links`, `click`,
   `finish` — and drives the browser a few steps.
3. Extracts page text, reasons over it, and returns a concise `result` plus the
   `steps` it took.

### Why Playwright + Anthropic instead of `browser-use`

`browser-use` is **not installable on this PC's Python 3.14** (`pip` finds no
compatible distribution). So this is the documented **fallback**: Playwright (real
Chromium) + the Anthropic SDK running a minimal tool-use browser agent. Same idea,
fewer moving parts. If you later run it on Python ≤3.12, you can swap in `browser-use`
without changing the HTTP contract below.

### Safety — read-only

The agent is constrained (system prompt **and** tool surface) to **read-only
browsing**: search, navigate, read, click links. It will **never** log into an
account, buy/order/pay for anything, or submit sensitive forms. If a goal would
require that, it refuses and says why.

## How to run

Requirements: Windows, `py` launcher (Python 3.14 here), internet access.

```powershell
cd D:\TuyaOpen-master1\pc_agent
# One command: creates .venv, installs deps, installs Chromium, launches on :8200
powershell -ExecutionPolicy Bypass -File .\run_pc_agent.ps1
```

`run_pc_agent.ps1` does `py -m pip install -r requirements.txt`,
`py -m playwright install chromium`, then launches the service on `0.0.0.0:8200`.

### The ANTHROPIC_API_KEY requirement

The agent reads `ANTHROPIC_API_KEY` from the **environment**. It is **never**
hardcoded.

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # set BEFORE launching (this shell only)
# or persist for your user:
setx ANTHROPIC_API_KEY "sk-ant-..."     # then open a new shell
```

- Without the key: **`GET /health` still works** (so BMO can see the agent is up),
  but `POST /task` returns a clear `503` telling you to set `ANTHROPIC_API_KEY`.
- With the key: `POST /task` runs the browser agent.

Optional env knobs: `PC_AGENT_MODEL` (default `claude-opus-4-8`; e.g.
`claude-haiku-4-5` for a cheaper/faster loop), `PC_AGENT_MAX_STEPS` (default `12`),
`PC_AGENT_PORT` (default `8200`), `PC_AGENT_HEADLESS=0` to watch the browser.

## The API

Listens on `0.0.0.0:8200`.

### `GET /health`
```json
{ "ok": true, "service": "bmo-pc-agent", "engine": "playwright+anthropic",
  "model": "claude-opus-4-8", "anthropic_key_present": true, "headless": true }
```

### `POST /task`
Request body: `{"goal": "..."}` (JSON; a bare `-d '{"goal":"..."}'` without a
Content-Type header is also accepted).

```json
{
  "result": "It is about 3:14 PM on Saturday in Seoul (KST, UTC+9).",
  "steps": [
    { "tool": "web_search", "input": {"query": "current time in Seoul"}, "output": "..." },
    { "tool": "finish", "answer": "It is about 3:14 PM ... in Seoul (KST, UTC+9)." }
  ]
}
```

- `503` if `ANTHROPIC_API_KEY` is unset.
- `429` if a browser task is already running (one at a time).
- `400` if `goal` is missing.

### Quick local test
```bash
curl http://127.0.0.1:8200/health
curl -X POST http://127.0.0.1:8200/task -d '{"goal":"find the current time in Seoul"}'
```

## The BMO → Armada → PC-agent flow

```
  You speak a goal to BMO
        │  (Tuya T5AI face/voice)
        ▼
  BMO firmware  ──WiFi──▶  Orange Pi 10.68.9.201 : Armada brain (:8099)
                                   │
                                   │  HTTP POST /task  (over the LAN)
                                   ▼
                         THIS Windows PC  :  pc_agent  (:8200)
                                   │  drives Chromium, reads the web (read-only)
                                   ▼
                         {"result": "...", "steps": [...]}
                                   │
                                   ▼
                    Armada → BMO shows / says the result
```

### This PC's LAN IP (so the Orange Pi can reach it)

**`10.68.11.27`** — from `ipconfig` (the `10.68.x` address). Same `10.68.0.0/16`
network as the Orange Pi (`10.68.9.201`, gateway `10.68.0.1`), so the Pi can reach it
directly. If the PC's IP changes on reconnect, re-check with `ipconfig`.

### How Armada calls it

Direct HTTP from the Pi (or anywhere on the LAN):

```bash
curl -X POST http://10.68.11.27:8200/task \
  -H "Content-Type: application/json" \
  -d '{"goal":"find the current time in Seoul"}'
```

Register it as a reusable Armada task the brain can fire (run on the Orange Pi):

```bash
# One-shot lookup task that BMO can trigger
armada add --name pc-lookup --kind task \
  'curl -s -X POST http://10.68.11.27:8200/task -H "Content-Type: application/json" -d "{\"goal\":\"find the current time in Seoul\"}"'

# Or, with the brain enabled, phrase it as a goal:
armada do "use the PC browser agent at http://10.68.11.27:8200/task to look up the current time in Seoul and report back"
```

`armada ls` / `armada logs <id>` show the task and its captured JSON result, which
BMO's Brain facet can render or speak.

---

## Paste-in block for BMO_HANDOFF.md / KIMI_INSTRUCTIONS.md

> ### PC Agent — "BMO controls the PC" (browser lookups on the Windows PC)
> A service on the Windows PC (`D:\TuyaOpen-master1\pc_agent\`) drives a real
> Chromium browser so BMO can look things up on the web and report back.
> **Engine:** Playwright + Anthropic/Claude (fallback — `browser-use` won't install
> on Python 3.14). **Read-only** browsing (no logins/purchases/forms).
> - **Run:** `powershell -ExecutionPolicy Bypass -File D:\TuyaOpen-master1\pc_agent\run_pc_agent.ps1`
> - **Requires:** `ANTHROPIC_API_KEY` in the PC's environment (**KIMI TODO** — set it,
>   never commit it). `/health` works without it; `/task` returns a 503 until it's set.
> - **PC LAN IP:** `10.68.11.27` (same `10.68/16` as the Pi at `10.68.9.201`).
> - **API (`:8200`):** `GET /health` → `{"ok":true,...}`;
>   `POST /task {"goal":"..."}` → `{"result":"...","steps":[...]}`.
> - **Armada calls it:**
>   `curl -X POST http://10.68.11.27:8200/task -H "Content-Type: application/json" -d '{"goal":"..."}'`
>   or `armada add --name pc-lookup --kind task '<that curl>'`.
