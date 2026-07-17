              SYSTEM ARCHITECTURE BLUEPRINT #T_BOT PROJECT /TESSWORKS PRODUCTION UNIT (TPU)

[ USER INTERFACE LAYER ]
  └── Flask Cockpit — http://localhost:5000
        ├── Cockpit          — live positions, P&L, TradingView charts
        ├── Trade Archive    — full historical ledger with filters
        ├── Market Intel     — live prices, news feed
        ├── System Code      — raw thinking-log tail, scan feed
        ├── Configuration    — risk limits, active pairs
        └── Dev Console      — chat-driven code editor (see 3.8)
              │
              ▼
[ COMMUNICATIONS LAYER ]
  └── chat_listener.py — conversational IMAP email listener + in-dashboard chat
        ├── AI Core: NVIDIA Nemotron-550B (primary) → Groq Llama 3.3 70B (fallback)
        ├── Persists conversational memory (chat_history.json)
        ├── Parses manual trade overrides → approved_queue.json
        └── Hard leverage/margin caps enforced even on manual overrides
              │
              ▼
[ EXECUTION & STATE ENGINE ] — isolated in .tess_diary/ to avoid reload loops
  └── scanner.py — background orchestration loop
        ├── Polls approved_queue.json every 5s (ApprovalGate)
        ├── Runs a full market scan every 5 minutes
        ├── Manages open positions: breakeven lock, trailing stop, TP/SL
        │
        ├──► [ LIQUIDITY SHIELD LAYER ]
        │      └── liquidity_engine.py — pre-trade orderbook validation
        │             ├── Spread gate — blocks execution on excessive bid-ask spread
        │             └── Liquidation cascade barrier — avoids entries too close
        │                 to large opposing liquidation pools
        │
        └──► [ STRATEGY LAYER — strategies.py ]
               Pure, stateless decision logic. No network calls, no file I/O.
               ├── 15m — confirmation only, never a standalone decision basis
               ├── 1h  — primary trend filter for swing trades
               ├── 4h  — primary trend filter for position (long-hold) trades
               └── AI Core: NVIDIA Nemotron-550B (structured JSON output)
                      — no fallback here; returns no-trade rather than
                        substituting another model for the actual decision
                            │
                            ▼
[ DEV TOOLING LAYER ] — separate from the trading path entirely
  └── dev_console.py — powered by Google Gemini 3.5, not Nemotron/Groq
        ├── Whitelisted file read/write, diff preview before any change
        └── Backup created automatically before every applied edit

[ OFFLINE / RESEARCH TOOLING ] — not part of the live scan loop
  └── multi_strategy_backtester.py — run manually against historical candles




1.1 Architectural Design Philosophy
    A. Decoupling of Concerns
The fundamental rule of the TESSWORKS architecture is the absolute separation of Input/Output (I/O) from calculation logic.
strategies.py is a pure mathematical module. It cannot execute network requests, write files to disk, or check local system states. It receives 
sanitized dataframes, calculates technical indicators, formats prompts, and queries the AI models.
scanner.py owns all I/O and process execution. It manages the background network threads, pulls candles from CCXT, starts the Flask server, and manages active database records.
   B. The Hidden Directory Hack (.tess_diary/)
To allow you to develop, edit, and hot-reload your code files (scanner.py, strategies.py, chat_listener.py) inside VS Code without causing the running trading bot 
to crash or reset, all files written by the bot are isolated inside .tess_diary/.
Because folders starting with a dot are automatically ignored by Flask's Werkzeug reloader, Tess can write to her logs, trade journals, and performance sheets thousands of 
times per hour without ever triggering a reload. However, the second you save a code change in your root directory, Flask performs a clean, automated hot-refresh of your active trading rules.
   C. Human-in-the-Loop (HITL) Gatekeeping
To eliminate the non-deterministic risk of AI-driven trading, Tess runs an approval gate. If a trading signal is generated, 
Tess writes the details to .tess_diary/pending_approvals.json and dispatches an email/Telegram alert. Only when you issue a 
manual APPROVE command is the trade moved to .tess_diary/approved_queue.json, where the parallel queue thread executes it instantly.

1.2 Transaction & Scan Life-Cycle (The 5-Minute Loop)
[ START CYCLE ]
  │
  ├──► 1. Read 'approved_queue.json' (Every 5 Seconds)
  │      └── IF approved trade found: Execute immediately. Clear queue.
  │
  ├──► 2. Check Cooldown & Kill-Switches
  │      └── IF daily loss >= $12.00 or cooldown active: Stand aside.
  │
  ├──► 3. Fetch Market Data (Every 5 Minutes)
  │      └── Query Binance public API via CCXT (Fallback: KuCoin if rate-limited)
  │
  ├──► 4. Calculate Technical Indicators (EMA, ATR, ADX, RSI, Crossovers, Pullbacks)
  │
  ├──► 5. Query NVIDIA Nemotron-550B Reasoning Engine
  │      └── Provide: 15m/1h/4h Snapshot + sqlite3 Failure History + Balance State
  │
  ├──► 6. Evaluate Liquidity Shield (On Proposed Signal)
  │      ├── Spread Check: If spread > 15 bps -> BLOCK trade.
  │      └── Heatmap Check: If entry sits on opposite liquidation pool -> BLOCK trade.
  │
  ├──► 7. Create Proposal (If cleared)
  │      └── Write to pending_approvals.json -> Dispatch Telegram/Email alert.
  │
[ END CYCLE ]




       Project Overview 
The Tess Flight Control System (FCS) is a modular, high-speed quantitative paper-trading application designed for macOS. 
It fully decouples your core analytical strategy layers from background execution loops, allowing you to safely test automated algorithms, manage system states, 
and monitor performance in real-time through a local web dashboard or private mobile Telegram integrations.


     Core File Directory Map:
- keys.py: Securely loads your private exchange keys, Gmail tokens, and LLM passwords dynamically from your local environment.
- config.py: Centrally registers your baseline risk parameters, active watchlists, and strategy-specific rules.
- strategies.py: Implements your technical indicators and formats the data snapshots evaluated by the Nvidia Nemotron-550B engine.
- liquidity_engine.py: Audits live orderbook spreads and liquidation heatmaps to protect your capital from slippage and cascades.
- scanner.py: Orchestrates the 5-minute background scanning thread, manages active open positions, and runs the web cockpit.
- chat_listener.py: Operates your mobile interfaces, including the Telegram Bot thread and the email approval dispatcher.
- multi_strategy_backtester.py: A standalone research tool to simulate and compare different quantitative strategies over historical data.



      Key Operational Functions:
1. RuleValidator.check_multi_timeframe_alignment (scanner.py)
- Purpose: This function determines if the trend is aligned across multiple timeframes. If the timeframes disagree or the market is moving sideways, it stands aside to protect capital.
- Example: If the 15-minute chart is trending downward, but the 1-hour and 4-hour charts are in a strong uptrend, this function flags a "MTF Mismatch" and blocks the trade.
  
2. calc_leverage_and_size (scanner.py)
- Purpose: Calculates your exact position size based on current volatility and a strict maximum risk limit.
- Example: If your account balance is 180 and yout max risk is 23.60, this function reads the ATR volatility. If volatility is high,
   it automatically widens your Stop Loss and decreases your contract size so you never risk more than $3.60 on the trade.
  
3. ApprovalGate.create_proposal (scanner.py / chat_listener.py)
- Purpose: Serves as your manual "Human-in-the-Loop" gatekeeper. It holds proposed trades in a pending queue until you authorize execution.
- Example: When Tess generates a trade setup, this function packages the entry, SL, and TP into an alert message.
  It sends this to your phone via Telegram with interactive buttons, pausing the trade until you tap APPROVE.




















  
