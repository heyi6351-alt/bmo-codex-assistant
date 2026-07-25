# BMO — AdventureX 2026 Demo Script

Two boards, one device: **Tuya T5AI-Board** = BMO's face/screen/body, **Orange Pi 3B**
(`10.68.9.201`) = BMO's always-on brain running the `armada` daemon. Tracks: **LiberNovo /
Desktop-Daemon** (armada) and **PandaAI Trading** (BMO's first feature).

Everything below runs **entirely on the local LAN** — no cloud involved.

---

## 0. Pre-demo checklist (2 min before judges arrive)

- [ ] Power on the **Orange Pi** first, then the **T5AI board**.
- [ ] BMO's face appears on the T5AI screen (mint background, blinking eyes, grin).
- [ ] On the Pi (or any laptop on the LAN):

      ssh orangepi@10.68.9.201
      armada status        # expect: up …, brain: ollama (or none — see §4)
      armada ls            # expect: trading-api (running), heartbeat (idle)

- [ ] Sanity-check the trading API:

      curl http://10.68.9.201:8100/health
      # → {"ok":true, "symbols":["EURUSD","GBPUSD","GOLD","US30","XAUUSD"]}

Autostart is already proven: power on → `armada.service` (systemd, enabled) → armada
respawns `trading-api` → BMO face boots and starts polling the Pi. No manual steps.

---

## 1. Act 1 — The daemon drives the body (LiberNovo / Desktop-Daemon)

**Line:** *"This little face has no cloud behind it. Everything you're about to see is the
Pi commanding the board over plain LAN HTTP."*

From any laptop (or on the Pi itself):

```bash
# BMO speaks — text toast pops up on BMO's screen, face animates SPEAKING
curl -X POST http://10.68.9.201:8099/v1/bmo \
  -H 'Content-Type: application/json' \
  -d '{"action":"say","arg":"Hello AdventureX! I am BMO. I live on this table now."}'
# (on the Pi, same thing: armada bmo say "Hello AdventureX!")

# Change BMO's expression
armada bmo face happy
armada bmo face surprised
```

**What judges see:** the Orange Pi pushing commands into a queue; BMO polls and obeys —
toast text on screen, face switching expressions. The daemon owns the body.

## 2. Act 2 — BMO the trading terminal (PandaAI Trading)

```bash
armada bmo open trader
```

**What judges see:** BMO's screen becomes a **live trading terminal** — candle chart, a big
direction pill (LONG green / SHORT red / flat neutral), RSI/MACD indicator chips, and the
strategy's reasoning, pulled from the analysis engine on the Pi (`:8100/signal`) every 5 s.

- **Tap the chart** to cycle symbols: XAUUSD → EURUSD → GBPUSD → US30.

```bash
armada bmo open apps    # the facet menu (every BMO capability, tappable)
armada bmo open home    # back to the plain face
armada bmo open brain   # armada status live on BMO's screen
```

**Line:** *"The agents collaborate: the analysis engine runs on the Pi under armada, BMO is
its face, and the MT5 executor on Windows is the next step — paper mode only. No real order
ever fires without explicit human approval."*

## 3. Act 3 — Give it a goal (armada brain)

```bash
armada do "summarize today's trading signals and pick the cleanest setup"
```

Then show `armada ls` / `armada digest` — the goal became a task with a result.

> ⚠️ **Mark:** this works once the **ollama brain is enabled** — see handoff §9.4
> (`ARMADA_BRAIN=ollama`, `llama3.2:1b`, systemd drop-in `brain.conf`). If `armada status`
> still shows `brain: none` at demo time, skip this act and say the brain ships next.

## 4. Closing beats

- Unplug nothing, open nothing — point out that the whole demo ran on LAN HTTP between two
  boards. **Local-first**: the daemon owns your data and drives a physical body.
- BMO's face is vector-drawn in code (zero image assets); the body stays instantly
  responsive because the heavy lifting lives on the brain.

---

## Talking points per track

**LiberNovo / Desktop-Daemon**
- A local, always-on daemon that owns your data and turns goals into running tasks.
- It doesn't just answer — it **drives a physical body**: every face change, toast, and
  screen you saw was the Pi commanding the T5AI over LAN HTTP.
- Services it manages (like the trading API) respawn on boot — the Pi is the single source
  of truth.

**PandaAI Trading**
- Agents collaborating: analysis engine on the Pi (armada-managed), BMO as its face, MT5
  executor on Windows as the next step.
- **Paper mode only** — analysis and signals are live; no real-money order executes without
  explicit human approval.
- BMO makes an abstract pipeline legible: the signal is a face and a chart you can tap.

## Known limitations (say them before judges ask)

- **Voice is deferred.** Tuya cloud voice is blocked (license lives in Tuya's China data
  center, unreachable over our network path — handoff §6). Chosen direction: **local voice
  via Armada** (mic → STT → armada brain → TTS), matching the local-first story.
  Re-licensing to a Western DC remains the alternative.
- **Market data is cached** unless the analysis runs with yfinance `live=1`; during the demo
  the charts may replay recent cached candles.
- **MT5 execution is not wired yet** — the Windows executor POSTing to the Pi API is the
  documented next step (handoff §7). Today BMO shows analysis/signals only.
- Brain (`armada do`) depends on the ollama install finishing on the Pi — §9.4.

---

## Act 4 — StepFun: the frontend factory (added 2026-07-25)
- On the Pi: `armada factory "a tiny landing page for BMO, a cute robot companion"`
- Watch it work: `armada factory-status <id>` — it plans, writes index.html/style.css/app.js,
  DRAWS original SVG art, checks its own work, and fixes itself (up to 3 iterations).
- Open the result live from the daemon: `http://10.68.9.201:8099/v1/factory/<id>/site/`
  (first demo run: id `6d803ee09b25`, 0 issues on iteration 1)
- Talking point: this is the StepFun loop — it writes, it draws, it verifies, unattended —
  running on the same always-on daemon that drives BMO and watches the markets. One brain,
  three jobs, all night.
- Quality lever: set `ARMADA_FACTORY_BASE_URL=https://api.kimi.com/coding/v1` +
  `ARMADA_FACTORY_API_KEY` + `ARMADA_FACTORY_MODEL=kimi-for-coding` in the armada drop-in
  for production-grade output (local 0.5b is the offline fallback).
