# BMO — Your Always-On AI Companion That Actually Lives With You

## Main purpose

AI assistants today are tabs in a browser on someone else's cloud. Close the tab, lose the assistant. It doesn't know your machines, it can't touch your world, and it stops the moment you sleep.

**BMO fixes that.** It is a pocket robot with a brain you *own*: a small always-on daemon (`armada`) running on an Orange Pi in your home. It stays awake 24/7, holds your data locally, runs your agents, and gives all of that a face, a voice, and a screen you can carry in one hand. No cloud account. No API keys required. Unplug the internet and it keeps working.

It answers three questions at once:
- **LiberNovo track** — what if your AI lived on a small always-awake host that's yours? (the daemon)
- **PandaAI track** — can agents do real, verifiable work? (autonomous trading)
- **StepFun track** — can AI write, draw, and *verify* its own frontend without a human? (the factory)

## Key features

### 1. A physical body for your AI (Tuya T5AI board)
- Animated BMO face drawn in pure LVGL — blinks, smiles, talks, gets surprised, reacts to the world. Zero image assets.
- Facet screens: **Brain** (daemon status), **Trader** (live markets), **Arcade** (games), apps launcher with animated tiles.
- The daemon remotely drives the body: change expression, push messages, switch screens — over plain Wi-Fi HTTP.
- BMO's face is emotionally wired to its work: it smiles when a signal flips LONG, flashes alert when risk spikes, and gets sleepy in flat chop — you read the market from its mood.

### 2. `armada` — the always-on personal daemon (Orange Pi 3B)
- **Task engine:** give it services to keep alive and jobs to run on a schedule; it respawns them after crashes and reboots.
- **Plain-language goals:** `armada do "check the gold feed every 30 minutes"` — an on-device LLM (qwen2.5:0.5B via ollama) turns your words into a running task. No cloud, no key.
- **Night-shift digest:** wake up to a summary of what it did while you slept, written by the local brain.
- **Remote body control:** `armada bmo face happy` / `armada bmo say "gm"` / `armada bmo open trader` — the daemon commands BMO's face and screen.
- **Hardened to survive:** memory-caged AI, swap protection, auto-recovery — the box cannot be taken down by its own brain.

### 3. BMO the autonomous trader (PandaAI)
- Autonomous signal engine running 24/7 as a daemon-managed service: RSI/MACD/ADX/Bollinger/EMA, regime gating, risk profiles, position sizing with honest "why not" reasons.
- Symbols: XAUUSD, EURUSD, GBPUSD, US30 — REST API (`/signal`, `/analysis`, `/candles`).
- **BMO becomes a live trading terminal:** animated candlestick chart with support/resistance, LONG/SHORT/FLAT badge, ENTRY/SL/TP ladder, indicator chips — tap to switch markets.
- **Multi-agent council:** every signal is reviewed by cooperating agents — the deterministic signal engine, risk profiles, an ML direction voter, and LLM reviewers arguing the bull, bear, and risk cases before a verdict is shown.
- **Execution bridge:** a MetaTrader5 executor on a Windows host takes the Pi's analysis and places the trades, reporting every execution back to the daemon — the Pi stays the single source of truth BMO reads. Paper mode by default; live orders only with the owner's explicit approval.

### 4. `armada factory` — the unattended frontend factory (StepFun)
- One sentence in, finished website out: it **plans**, **writes** HTML/CSS/JS, **draws** original art, **checks its own work** (reference checks + headless-browser screenshot + model critique), and **fixes** itself — iterating without a human until the page is clean.
- **It literally looks:** a headless browser renders the page and a multimodal model critiques the actual screenshot — layout, buttons, image fit — then the factory repairs what it saw.
- Original assets only: every image is generated in-house (generative art from the plan's palette).
- The result is served straight from the daemon.
- Pluggable brain: offline 0.5B model by default; one env var switches to a frontier coding model.

### 5. Voice — talk to BMO, it talks back
- On-device wake words: "Hey Tuya" / "Hi Tuya" / "你好涂鸦" / "小智同学".
- Full conversational loop: BMO's mic captures your question, speech-to-text transcribes it, the armada brain thinks, and BMO answers out loud through its speaker — mouth animating while it speaks.
- Voice navigation: open any facet by name — "trader", "brain", "arcade", "menu".

### 6. ZILO Smart Ring — your daemon, on your finger
- Tap or whisper to the ring from miles away: "check the markets", "lock my desktop", "build a page for tomorrow". The daemon at home executes; the ring buzzes back the result.
- **Away mode:** the daemon manages your desktop while you're out — keeps your builds, downloads and trades running under your rules, locks the machine on demand.
- **Done-notifications:** when a task finishes, your ring buzzes and BMO has the digest waiting on-screen when you're back.
- The ring is a client of the same local-first brain: your agent never leaves your house, but it follows you everywhere.

## How it works

```
Tuya T5AI board (body)  ◄── plain HTTP over Wi-Fi ──►  Orange Pi 3B (brain)
face · mics · speaker · touch · LVGL facets              armada daemon :8099 · trading API :8100 · ollama brain
        ▲                                                        ▲
        └──────────── ZILO Smart Ring (remote, encrypted tunnel) ┘
```

The body polls the brain for status and commands; the brain owns the decisions; the ring reaches the brain over a secure tunnel from any network. Everything is local-first: the whole system runs with the WAN cable pulled.

## Demo in 6 steps

1. Power on both boards — face boots, daemon boots, agents respawn. Nothing to press.
2. `armada bmo face happy` — BMO smiles. `armada bmo say "gm"` — toast on screen.
3. `armada bmo open trader` — live terminal; tap chart to switch markets.
4. `armada do "<your own goal>"` — the local brain plans and runs it.
5. `armada factory "a site for our team"` — watch it write, draw, look, fix; open the result.
6. `armada digest` — the report of its night shift.

## Stack

Go (daemon · REST · factory) · Python/Flask/pandas (trading) · C + TuyaOpen SDK + LVGL 9 (firmware) · ollama + qwen2.5:0.5B · systemd · Orange Pi 3B (RK3566) · Tuya T5AI (BK7258) · ZILO Smart Ring (BLE companion)

**Repos:** firmware `github.com/SbxTheDead/advx-bmo` · daemon `github.com/SbxTheDead/armada-advx`
