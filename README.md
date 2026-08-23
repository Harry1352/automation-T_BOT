# TESS — Autonomous Crypto Trading Cockpit
                                                                         T_BOT PROJECT /TessWorks Production Unit (TPU)

> **TESSWORKS Quantitative Systems**
> Version: v13.0 · Dual-Model Reasoning · Dev Console Enabled
> A modular, multi-timeframe, human-in-the-loop paper-trading agent — with an embedded AI assistant that edits its own source code.

---

## 1.0 Overview

Tess is a locally-run autonomous trading cockpit for crypto perpetuals (ETH, SOL, BTC, XAUT), built around a strict separation of concerns: raw market data ingestion, indicator computation, AI-driven trade decisions, position execution, and risk enforcement are each isolated into their own layer. No single component both decides a trade and executes it unchecked — every path, whether AI-generated or manually issued by voice/chat, passes through the same hard-capped risk gate.

Currently runs in **paper trading mode**. Delta Exchange India credentials are present in the credential layer for a future live-execution phase, but no live order routing exists yet.

---

## 2.0 System Architecture

```text
[ USER INTERFACE LAYER ]
  └── Flask Cockpit — http://localhost:5000
        ├── Cockpit          — live positions, P&L, TradingView charts
        ├── Trade Archive    — full historical ledger with filters
        ├── Market Intel     — live prices, news feed
        ├── System Code      — raw thinking-log tail, scan feed
        ├── Configuration    — risk limits, active pairs
        └── Dev Console      — chat-driven code editor (see 2.4)
              │
              ▼
[ COMMUNICATIONS LAYER ]
  └── chat_listener.py — conversational IMAP email listener + in-dashboard chat
        ├── Persists conversational memory (chat_history.json)
        └── Parses manual trade overrides → approved_queue.json
              │
              ▼
[ EXECUTION & STATE ENGINE ] — isolated in .tess_diary/ to avoid reload loops
  └── scanner.py — background orchestration loop
        ├── Polls approved_queue.json every 5s (ApprovalGate)
        ├── Runs a full market scan every 5 minutes
        ├── Manages open positions: breakeven lock, trailing stop, TP/SL
        │
        └──► [ STRATEGY LAYER — strategies.py ]
                Pure, stateless decision logic. No network calls, no file I/O.
                ├── 15m  — confirmation only, never a standalone decision basis
                ├── 1h   — primary trend filter for swing trades
                ├── 4h   — primary trend filter for position (long-hold) trades
                └── AI Core: NVIDIA Nemotron-550B (structured JSON output)
                       └── Fallback: Groq Llama 3.3 70B on failure
```

---

## 3.0 Core Features

### 3.1 Modular Strategy Decoupling (`strategies.py`)
All indicator math (EMA9/20, RSI, ATR, ADX), the multi-timeframe policy, and the LLM prompt live in a single stateless module with zero network or disk dependencies. `scanner.py` fetches candles and hands them to `strategies.py`, which returns a decision and nothing else — meaning the strategy can be tuned or unit-tested in complete isolation from execution and the dashboard.

### 3.2 Disciplined Multi-Timeframe Policy
Tess deliberately does not look at anything faster than 15 minutes. Every decision is required to justify itself against its primary timeframe — 1h for a `swing` call, 4h for a `position` call — with 15m usable only to sanity-check the entry candle. Reasoning that leans on 15m alone is treated as a red flag and steered toward `skip`.

### 3.3 Primary Reasoning + Fallback (not a consensus vote)
Nemotron-550B (NVIDIA NIM, streaming, forced `json_object` output) is the primary decision engine on every scan. Groq's Llama 3.3 70B is a sequential fallback that only engages if the primary call fails outright — it is not run in parallel and does not vote alongside Nemotron.

### 3.4 Human-in-the-Loop Approval Gate
Manual trade commands — issued via the dashboard chat or email — are parsed, sized, and placed into `approved_queue.json`, then picked up by a dedicated 5-second polling thread (`ApprovalGate`) for execution. Critically, hard leverage and position-size caps apply identically to AI-generated and manually-issued trades — a forceful or persistent chat command cannot push the system past its configured risk ceiling.

### 3.5 Cognitive Memory (`MemorySynthesizer`)
Losing trades are clustered by pair, direction, and market regime (RSI/ADX buckets) into a rolling "lessons" cache, which is injected into every future prompt so Tess is explicitly warned away from patterns that have repeatedly lost money.

### 3.6 In-Dashboard Dev Console
A separate chat panel, powered by **Google Gemini 3.5** — a deliberately different model from the trading core, so a code-editing session can never influence a live trade decision. Points at a whitelisted set of source files, proposes full-file rewrites with a unified diff for review, and only writes to disk after an explicit "Apply" click. Every apply is preceded by a timestamped backup.

### 3.7 Credential Isolation & Safe Hot-Reload
All secrets live in a git-ignored `.env`, loaded through `keys.py` and never touched by the Dev Console's file whitelist. Runtime state, logs, and trade history are redirected into a hidden `.tess_diary/` directory so Flask's file-watcher doesn't trigger reload loops while actively developing.

---

## 4.0 Installation

### 4.1 Environment

```bash
cd ~/Desktop/T_BOT
source tess_env/bin/activate
pip install ccxt pandas numpy flask requests python-dotenv openai
```

### 4.2 Environment Variables

Create `.env` in the project root:

```env
DELTA_API_KEY=your_delta_exchange_api_key
DELTA_API_SECRET=your_delta_exchange_secret
NVIDIA_NEMOTRON_KEY=your_nvidia_nim_key
GROQ_API_KEY_2=your_groq_fallback_key
GOOGLE_STUDIO_API_KEY=your_google_ai_studio_key
TESS_GMAIL=your_bot_gmail_address
TESS_GMAIL_APP_PASSWORD=your_gmail_app_password
YOUR_EMAIL=your_personal_email
```

### 4.3 Run

```bash
caffeinate python3 scanner.py
```

- Dashboard: `http://localhost:5000`
- Every panel — Cockpit, Trade Archive, Market Intel, System Code, Configuration, Dev Console — lives in this single interface.

---

## 5.0 Roadmap

Not yet implemented — listed here deliberately as future work, not current capability:

- **Live execution** via Delta Exchange India (currently paper-mode only)
- **Orderbook microstructure shield** — pre-trade spread and liquidation-cascade checks
- **Telegram command interface**, alongside the existing email/dashboard channels
- **Multi-strategy backtester** for comparing EMA-based logic against alternative strategies on historical data

---

## 6.0 Disclaimer

Tess is an educational quantitative research project for automated paper trading and system simulation. Nothing in this repository — code, strategy logic, or AI-generated reasoning — constitutes financial or investment advice. Algorithmic trading, leverage, and cryptocurrency derivatives carry substantial risk of capital loss. Historical or simulated performance is not indicative of future results.
