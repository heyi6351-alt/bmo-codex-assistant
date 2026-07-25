# Graph Report - tradingaI  (2026-07-09)

## Corpus Check
- 56 files · ~76,417 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1032 nodes · 2844 edges · 45 communities (40 shown, 5 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 261 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `fd7ce998`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]

## God Nodes (most connected - your core abstractions)
1. `RiskProfile` - 54 edges
2. `Signal` - 53 edges
3. `str` - 52 edges
4. `Candle` - 49 edges
5. `float` - 43 edges
6. `Update` - 39 edges
7. `generate_signal()` - 38 edges
8. `_reply()` - 35 edges
9. `NewsItem` - 35 edges
10. `Trade` - 35 edges

## Surprising Connections (you probably didn't know these)
- `str` --uses--> `Candle`  [INFERRED]
  _gated_wf_verify.py → core/models.py
- `bool` --uses--> `RiskProfile`  [INFERRED]
  analysis/backtest.py → risk/profiles.py
- `float` --uses--> `RiskProfile`  [INFERRED]
  analysis/optimizer.py → risk/profiles.py
- `str` --uses--> `RiskProfile`  [INFERRED]
  analysis/optimizer.py → risk/profiles.py
- `RiskProfile` --uses--> `RiskProfile`  [INFERRED]
  analysis/optimizer.py → risk/profiles.py

## Import Cycles
- 1-file cycle: `bot/jobs.py -> bot/jobs.py`
- 1-file cycle: `data/dukascopy.py -> data/dukascopy.py`

## Communities (45 total, 5 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.07
Nodes (58): Account, _append_trade_csv(), build_accounts(), _close_units(), console_table(), Engine, _finalize(), _finalize_open_to_market() (+50 more)

### Community 1 - "Community 1"
Cohesion: 0.06
Nodes (68): _breakout_retest(), _breakout_setup(), confluence(), _decimals(), _first_entry_setup(), generate_signal(), _h1l1_first_entry(), _h2l2_second_entry() (+60 more)

### Community 2 - "Community 2"
Cohesion: 0.09
Nodes (62): build_cc(), build_snapshot(), decide(), _fmt(), _map_tf(), _no(), bool, float (+54 more)

### Community 3 - "Community 3"
Cohesion: 0.12
Nodes (60): _ai_bool(), _authorized(), _build_help(), cmd_addadmin(), cmd_admins(), cmd_ai(), cmd_aiapprove(), cmd_aireject() (+52 more)

### Community 4 - "Community 4"
Cohesion: 0.11
Nodes (46): bytes, Annotated trade-chart images for Telegram — candles + EMAs + support/resistance, Render the last ``n`` candles with the trade's levels overlaid.     Returns PNG, render_trade_chart(), adl(), adx(), atr(), bollinger() (+38 more)

### Community 5 - "Community 5"
Cohesion: 0.06
Nodes (41): _assistant_dict(), deep_research(), int, str, Deep web-research loop.  A tool-calling agent (NVIDIA model with reliable functi, Returns {report, sources, note}., _run_tool(), Application (+33 more)

### Community 6 - "Community 6"
Cohesion: 0.14
Nodes (38): add_admin(), add_trade(), all_open_trades(), chat_news_seen(), chats_with_digests(), _connect(), get_config(), get_settings() (+30 more)

### Community 7 - "Community 7"
Cohesion: 0.08
Nodes (40): _afford_estimate(), _afford_reason(), _balance(), _equity(), _exposure_cap(), _exposure_ok(), _heat_ok(), _llm_assess() (+32 more)

### Community 8 - "Community 8"
Cohesion: 0.09
Nodes (38): daily_trend(), detect_regime(), _gate_bucket(), _htf_adx(), htf_trend(), _lev_str(), main(), _notify_open() (+30 more)

### Community 9 - "Community 9"
Cohesion: 0.11
Nodes (35): _acquire_lock(), available(), chat(), complete(), _first_balanced_object(), _get_client(), _global_reserve(), list_model_ids() (+27 more)

### Community 10 - "Community 10"
Cohesion: 0.15
Nodes (33): aggregate_plan(), build_candidates(), build_ticket(), Candidate, _clean_ladder(), _dec(), deterministic_ticket(), display_name() (+25 more)

### Community 11 - "Community 11"
Cohesion: 0.21
Nodes (30): assess_trade_threat(), _choose_headline(), _enrich(), _news_context(), bool, int, NewsItem, RiskProfile (+22 more)

### Community 12 - "Community 12"
Cohesion: 0.14
Nodes (31): _alpha_vantage_news(), _av_time(), _canonical_key(), fetch_news(), _get_vader(), label_for(), news_key(), bool (+23 more)

### Community 13 - "Community 13"
Cohesion: 0.11
Nodes (32): _ai_account_ctx(), _ai_daemon_tick(), _ai_engine_ctx(), _ai_open_combos(), _ai_queue_approval(), _ai_realized_today(), _ai_recent_ctx(), _ai_settings() (+24 more)

### Community 14 - "Community 14"
Cohesion: 0.12
Nodes (30): edge_report(), expected_longest_loss_run(), main(), _max_drawdown_R(), mc_drawdown_envelope(), min_trl(), psr(), DataFrame (+22 more)

### Community 15 - "Community 15"
Cohesion: 0.17
Nodes (28): _conf_bar(), _disp(), esc(), fmt_backtest(), fmt_models(), fmt_news(), fmt_optimize(), fmt_price() (+20 more)

### Community 16 - "Community 16"
Cohesion: 0.10
Nodes (26): _choppiness(), _closed_result(), _exhaustion(), get_candles(), _last_closed_candle(), manage_open(), modify_sltp(), _mt5_candles() (+18 more)

### Community 17 - "Community 17"
Cohesion: 0.14
Nodes (22): backtest(), _default_cost(), bool, float, int, RiskProfile, str, Backtester — replays the deterministic signal engine over historical candles.  F (+14 more)

### Community 18 - "Community 18"
Cohesion: 0.14
Nodes (24): digest_job(), _digest_signature(), _digest_source(), _mins_since(), monitor_job(), _news_due(), news_job(), bool (+16 more)

### Community 19 - "Community 19"
Cohesion: 0.17
Nodes (18): build(), _decompress(), download_ticks(), _fetch_hour(), _month_starts(), bool, bytes, DataFrame (+10 more)

### Community 20 - "Community 20"
Cohesion: 0.13
Nodes (19): _bump_daily(), _daily_count(), evaluate_combo(), event_blackout(), _fetch_calendar(), macro_bias(), market_status(), _news_bias() (+11 more)

### Community 21 - "Community 21"
Cohesion: 0.16
Nodes (18): can_afford(), _close_position(), connect(), _fill_mode(), _ladder(), open_combo(), _place_ai(), bool (+10 more)

### Community 22 - "Community 22"
Cohesion: 0.25
Nodes (14): _api_key(), _base_url(), nvidia_keys(), provider_order(), int, str, FREE multi-provider LLM failover registry.  We run on FREE tiers only. Any singl, # NOTE: OpenRouter gutted its free tier (2026) — nearly all ':free' variants are (+6 more)

### Community 23 - "Community 23"
Cohesion: 0.23
Nodes (14): _ddg_available(), _ddg_search(), _exa_client(), fetch_url(), bool, int, str, Web search + page fetch — the tools that power "deep investigation".  Search:  T (+6 more)

### Community 24 - "Community 24"
Cohesion: 0.15
Nodes (12): 0. The core principle, 1. The improvement loop (run this for EVERY proposed change), 2. What we've VALIDATED (trust these), 3. Proven HARMFUL — do NOT repeat (each was backtested and made things worse), 4. Roadmap — validated NEXT builds (highest value first), 5. Open bug backlog (triaged 2026-07-08, not yet fixed), 6. FIXED 2026-07-08 (this pass), 7. FIXED 2026-07-09 (loss post-mortem + audit fixes) (+4 more)

### Community 25 - "Community 25"
Cohesion: 0.21
Nodes (13): adopt_orphans(), finalize(), _fmt(), _fresh_combo(), _fresh_state(), load_state(), _log_csv(), _notify_close() (+5 more)

### Community 26 - "Community 26"
Cohesion: 0.15
Nodes (12): 1. Install Python 3.11+ and dependencies, 2. Get your keys (all have free tiers), 3. Configure, 4. Run, 🪙 AI Gold Trading Bot, 🧠 AI models (switch live with `/model`), Architecture, Commands (+4 more)

### Community 27 - "Community 27"
Cohesion: 0.24
Nodes (11): council_vote(), format_votes(), _one_vote(), panel_list(), float, int, str, Model council — a multi-LLM voting panel + role specialisation.  Research (Stock (+3 more)

### Community 28 - "Community 28"
Cohesion: 0.44
Nodes (9): asset_keyboard(), currency_keyboard(), _grid(), model_keyboard(), int, Inline keyboards for switching asset, quote currency, and risk profile., risk_keyboard(), InlineKeyboardButton (+1 more)

### Community 29 - "Community 29"
Cohesion: 0.28
Nodes (7): Analysis layer: technical indicators, signal engine, LLM pipeline., RiskProfile, str, Model tournament — several NVIDIA models read the same setup and vote.  Inspired, mode='all' → every catalogued model; otherwise the fast (non-🐢) set., run_tournament(), tournament_models()

### Community 30 - "Community 30"
Cohesion: 0.33
Nodes (8): by_id(), by_slug(), is_tool_capable(), ModelInfo, bool, str, Curated catalog of NVIDIA-hosted models for trading analysis.  All ids below wer, True if we know this model does reliable tool calls (default True if unknown).

### Community 31 - "Community 31"
Cohesion: 0.22
Nodes (8): Gold/XAUUSD Strategy — "Trend-Continuation, Risk-First" (v1), Risk rules (risk-first), Still to build (next phase — only matters once validated), The data blocker (do this first), The rules (selective), v2 — Active Intraday "pullback" mode (default, `STRATEGY_MODE=pullback`), ⚠️ Validation gate — NOT yet passed, What changed from the old engine

### Community 32 - "Community 32"
Cohesion: 0.33
Nodes (6): main(), float, int, str, WALK-FORWARD validation — separates a REAL edge from a lucky backtest (research:, run()

### Community 33 - "Community 33"
Cohesion: 0.33
Nodes (5): Guardian + Re-Entry Workflow (research-backed), Honest caveats, Part A — Protective guardian (early exit BEFORE the hard stop), Part B — Anti-churn re-entry gate, What was verified (unit test)

### Community 34 - "Community 34"
Cohesion: 0.33
Nodes (6): _ai_manage_assess(), _llm_advisor_loop(), Parse a model-supplied number that may be None, '', a string, or already numeric, Ask the AI (Kimi failover chain via llm.complete, model=None) how to MANAGE this, Background thread: every LLM_INTERVAL, ask the AI MANAGER about EVERY open trade, _to_float()

### Community 35 - "Community 35"
Cohesion: 0.33
Nodes (6): _danger(), _guard_frac(), guard_open(), Is this OPEN trade turning against us? Returns a reason to close, or None., Run the protective check on every open trade (called every heartbeat)., Per-strategy adverse fraction for the guardian's early-exit, tightened on M1.

### Community 36 - "Community 36"
Cohesion: 0.40
Nodes (5): Popen, main(), str, Launch the MT5 trader for MULTIPLE symbols at once, each in its own process.  Ea, _spawn()

### Community 37 - "Community 37"
Cohesion: 0.50
Nodes (3): permissions, additionalDirectories, allow

### Community 38 - "Community 38"
Cohesion: 0.50
Nodes (4): _drawdown_kill(), The ONLY drawdown-driven action (playbook 2026-07-09). Pause NEW entries IFF liv, (n, per-trade t-stat on mean(R)>0) over the last `window` CLOSED trades in the t, _rolling_tstat()

## Knowledge Gaps
- **54 isolated node(s):** `allow`, `additionalDirectories`, `disabledMcpjsonServers`, `bytes`, `bool` (+49 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Candle` connect `Community 0` to `Community 1`, `Community 4`, `Community 7`, `Community 8`, `Community 11`, `Community 13`, `Community 14`, `Community 16`, `Community 21`?**
  _High betweenness centrality (0.080) - this node is a cross-community bridge._
- **Why does `Signal` connect `Community 11` to `Community 1`, `Community 2`, `Community 10`, `Community 15`?**
  _High betweenness centrality (0.060) - this node is a cross-community bridge._
- **Why does `generate_signal()` connect `Community 1` to `Community 0`, `Community 35`, `Community 4`, `Community 11`, `Community 13`, `Community 14`, `Community 17`, `Community 20`, `Community 29`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Are the 43 inferred relationships involving `RiskProfile` (e.g. with `bool` and `int`) actually correct?**
  _`RiskProfile` has 43 INFERRED edges - model-reasoned connections that need verification._
- **Are the 43 inferred relationships involving `Signal` (e.g. with `bool` and `int`) actually correct?**
  _`Signal` has 43 INFERRED edges - model-reasoned connections that need verification._
- **Are the 35 inferred relationships involving `Candle` (e.g. with `Candle` and `DataFrame`) actually correct?**
  _`Candle` has 35 INFERRED edges - model-reasoned connections that need verification._
- **What connects `allow`, `additionalDirectories`, `disabledMcpjsonServers` to the rest of the system?**
  _316 weakly-connected nodes found - possible documentation gaps or missing edges._