# 🪙 AI Gold Trading Bot

A Telegram bot that delivers **gold (XAU/USD) news, deep AI analysis, and trading signals with explicit risk** — powered by **NVIDIA-hosted LLMs**. The tracked asset, quote currency, and risk profile are all switchable **live from Telegram**. Designed to run on your **Windows PC now** and drop onto a **24/7 VPS / Docker** later with zero code changes.

> ⚠️ **NOT FINANCIAL ADVICE.** This bot is for educational and informational purposes only. Trading gold/FX (including CFDs) is high-risk and you can lose all your capital. Past performance does not guarantee future results. Always do your own research and consult a licensed professional. See [DISCLAIMER](#-disclaimer).

---

## What it does

- **📰 News + sentiment** — aggregates macro/gold news (Alpha Vantage + RSS: Investing.com, Kitco, FXStreet, ForexLive) and scores sentiment.
- **🔬 Deep web research** — an LLM agent runs many searches (Tavily) and reads full pages, cross-verifying claims before drawing conclusions.
- **📊 Signals with risk** — a deterministic technical engine computes the numbers (entry, ATR-based stop, laddered TPs, R:R, position-size formula); the LLM team reasons over them.
- **🤖 Multi-agent analysis** — Technical Analyst → News/Sentiment Analyst → Bull vs Bear debate → Risk Manager → Signal Synthesizer (TradingAgents-style).
- **🚨 Open-trade guardian** — register a live trade with `/track`; the bot watches price and scans news, and pings you **urgently** if something threatens *that* position.
- **🎚️ Live switching** — change asset (`/asset`), currency (`/currency`), and risk profile (`/risk`) from chat; changes apply immediately.
- **⏰ Three delivery modes** — scheduled digests, real-time alerts, and on-demand commands.

---

## Architecture

```
Telegram (commands + inline menus)
        │
   bot/ (handlers, keyboards, formatting, scheduled jobs)
        │
   analysis/ ── indicators → deterministic signal engine
        │      └─ LLM pipeline (agents.py) ── deep research (research.py)
        │                                        │
   data/ ── market.py (Twelve Data + yfinance)   ├─ websearch.py (Tavily + Trafilatura)
        │   news.py (Alpha Vantage + RSS + VADER) │
        │                                         │
   analysis/llm.py ── NVIDIA NIM (OpenAI-compatible)
        │
   core/ ── db.py (SQLite) · models.py · state.py    risk/ ── profiles.py
```

The LLM **never invents prices** — every numeric field comes from the deterministic engine; the models only reason, debate, and risk-check.

---

## Setup

### 1. Install Python 3.11+ and dependencies

```powershell
# Windows PowerShell, from the project folder
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Get your keys (all have free tiers)

| Key | Where | Required? |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` | ✅ required |
| `TELEGRAM_ALLOWED_USER_IDS` | [@userinfobot](https://t.me/userinfobot) | ✅ strongly recommended |
| `NVIDIA_API_KEY` | [build.nvidia.com](https://build.nvidia.com) → Settings → API Keys (`nvapi-…`) | ✅ required |
| `TWELVEDATA_API_KEY` | [twelvedata.com](https://twelvedata.com/pricing) | ⭐ recommended |
| `ALPHAVANTAGE_KEY` | [alphavantage.co](https://www.alphavantage.co/support/#api-key) | ⭐ recommended |
| `TAVILY_API_KEY` | [app.tavily.com](https://app.tavily.com) | ⭐ recommended |
| `EXA_API_KEY`, `FIRECRAWL_API_KEY`, `OANDA_*` | optional extras | ⬜ optional |

### 3. Configure

```powershell
copy .env.example .env
# then edit .env and paste your keys
```

### 4. Run

```powershell
python main.py
```

Open Telegram, message your bot, send `/start`.

---

## Commands

| Command | What it does |
|---|---|
| `/start`, `/help` | Welcome + command list |
| `/price` | Current price of the tracked asset |
| `/news` | Latest news digest with sentiment |
| `/social` | Reddit (+ optional X) chatter and sentiment |
| `/signal` | Compact multi-timeframe signal card (sends a trade ticket too if actionable) |
| `/ticket` | Broker-style call: `BUY/SELL MANUAL AROUND … / SL / TP×5` (risk-plan tournament) |
| `/analyze [question]` | Deep web research + full multi-agent reasoning |
| `/backtest [tf]` | Backtest the signal engine on history (win-rate, avg R, profit factor, drawdown, in/out-of-sample) |
| `/optimize [tf]` | Auto-tune strategy params via grid search, validated out-of-sample (no AI calls) |
| `/tournament [all]` | Several AI models vote on the trade; add `all` to include every model |
| `/asset` | Menu to switch the tracked asset (gold → silver, EURUSD, BTC…) |
| `/currency` | Menu to switch the quote currency (USD → EUR, GBP…) |
| `/model` | Switch the AI model (Kimi, DeepSeek, MiniMax, Qwen, GLM…); `/model all` lists every free model |
| `/risk` | Set risk profile: conservative / moderate / aggressive (applies instantly) |
| `/track` | Register a live trade for the guardian to monitor |
| `/trades` | List monitored trades |
| `/untrack <id>` | Stop monitoring a trade |
| `/digest on\|off` | Toggle scheduled digests |
| `/settings` | Show current asset, currency, risk, model, schedule |

**Owner-only** (set `TELEGRAM_OWNER_ID`): `/setchannel @ch` · `/post [text]` (broadcast a signal or message) · `/addadmin <id>` · `/removeadmin <id>` · `/admins`.

---

## 🧠 AI models (switch live with `/model`)

Every model runs **free** on NVIDIA NIM. Defaults chosen from a live latency test against your key:

| Model | Best for | Notes |
|---|---|---|
| **Kimi K2.6** *(default brain)* | All-round analysis + tool use | Fast (~2s), agentic 1T-MoE |
| **Qwen 3.5 122B** *(default research)* | Web-research tool loop | Most consistent function-calling |
| GLM-5.1 / Nemotron Super 49B | Balanced analyst | Reliable, snappy |
| Llama 3.3 70B | Speed / safe fallback | Fastest (~0.5s) |
| DeepSeek V4 Pro · MiniMax M3 · Nemotron-3 Ultra | Deepest reasoning | 🐢 Slow (>30s) — great for `/analyze`, sluggish for `/signal` |

`/model` shows the curated picker; `/model all` lists all 120+ free models; `/model <id>` sets any of them. The bot auto-falls-back to Llama 3.3 if a chosen id ever becomes unavailable.

## Deploy to a 24/7 VPS (later)

The bot uses **long-polling**, so it needs **no public IP, domain, or webhook** — it works the same on a VPS as on your PC.

```bash
# On any small Linux VPS
docker build -t gold-bot .
docker run -d --restart unless-stopped --env-file .env --name gold-bot \
  -v $(pwd)/storage:/app/storage gold-bot
```

The SQLite database lives in `storage/` (mounted as a volume so settings/trades survive restarts).

---

## 📜 Disclaimer

This software is provided for **educational and informational purposes only** and does **not** constitute financial, investment, or trading advice, nor a recommendation or solicitation to buy or sell any instrument. Trading foreign exchange, gold, and CFDs on margin carries a **high level of risk** and may not be suitable for all investors; you can lose **more than your initial deposit**. AI-generated analysis can be wrong, biased, or based on stale data. **You** are solely responsible for your trading decisions. Always do your own research and consult a licensed financial professional. The authors accept no liability for any loss arising from use of this software.
