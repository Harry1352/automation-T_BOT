# TESS — Handoff Summary (for new chat)

## Where things stand

**Cleanup:** T_bot folder is clean. Dead/legacy files archived in `legacy/<date>/`, nothing deleted. `liquidity_engine.py` restored (it's tested by `test_rules.py`, not actually dead). `cleanup_tess_folder.py` updated and reusable.

**API health check:** `api_healthcheck.py` (root) verified working — Nemotron, Groq, Binance, KuCoin, CoinGecko, Gmail SMTP, ntfy, Telegram all confirmed live. Gemini hit a real quota 429 (Google AI Studio free tier — only affects `dev_console.py`, not live trading). Kraken had a transient connection reset (fallback-of-a-fallback, not blocking). Fixed two stale model IDs (Groq's `llama-3.3-70b-versatile` → `openai/gpt-oss-120b`; Gemini's `gemini-2.5-pro` → `gemini-3.1-pro-preview`) — **`dev_console.py` still needs this same Gemini model fix applied**, not yet done.

**Security flag, not yet fixed:** Telegram bot token is hardcoded in plaintext in `chat_listener.py` instead of `keys.py`/`.env` like every other credential. Move it.

## New strategy system — built and unit-tested this session

Four independent, mathematically-defined strategy families in `strategies/`, each extending `_shared/` common math. Adapted from Anish Singh Thakur (Booming Bulls Academy)'s price-action curriculum where it transfers to crypto, honestly re-derived where it doesn't (see the locked PDF spec for the full adaptation log).

```
strategies/
  _shared/
    indicators.py            EMA/RSI/ATR/ADX (ported from base.py) + MACD, Bollinger squeeze,
                              VWAP, Stoch RSI, volume confirmation, RSI divergence
    swing_detector.py        N-bar fractal pivots — shared source of truth for all "swing" logic
    sr_zones.py               S/R zone clustering, scored by touch count + recency decay
    candlestick_patterns.py  Full taxonomy: 5 single-candle, 5 two-candle, 6 three-candle patterns
  technical_analysis/strategy.py   EMA9/20/200 + MACD + RSI divergence + VWAP/StochRSI, 3 signal modes
  price_action/strategy.py         12 chart patterns (double/triple top-bottom, H&S/inverse,
                                    triangles, rectangle, flags) + candle confirmation + confluence
  fibonacci/strategy.py            Golden Zone entries, extension-based TP, invalidation rule
  price_action_smc/strategy.py     BOS/CHoCH, order blocks, FVG, liquidity sweeps, PDH/PDL,
                                    crypto-adapted correlated-asset divergence
  backtest_harness.py              Unified sweep + walk-forward validator for all 4 families
  family_dispatch.py                Safe opt-in switch — current live system UNCHANGED unless
                                     you explicitly flip a pair to a validated new family
```

**Every module was smoke-tested with synthetic data in this session** (not real market data — my sandbox can't reach Binance). All ran clean, bugs found were fixed live (a tuple-unpacking bug in `price_action_smc`, an unused-parameter bug in `fibonacci`, a stray import in `sr_zones`). **One important honesty note:** a harness test run showed a 95-100% win rate — that's an artifact of unrealistically clean synthetic trend data, not a real result. **Nothing here is validated against real market performance yet.** That only happens when you run `backtest_harness.py` on your machine with real Binance history.

### What's deliberately NOT implemented (flagged, not faked)
`price_action/strategy.py` bottom comment block lists Phase 2 patterns not yet built: Rounding Top/Bottom, V-Top/V-Bottom, Pennant, Cup and Handle, Rising/Falling Wedge.

## Next steps, in order

1. Extract `strategies_new.zip` into your T_bot `strategies/` folder (merges alongside existing `base.py`, `bitcoin.py`, etc. — nothing existing gets overwritten)
2. Run `python3 strategies/backtest_harness.py --pair BTC/USDT --family all` (repeat per pair) — **this is the first real test against actual market data**
3. Review results honestly — some family/pair combos may show no edge, same as ETH/XAUT already did under the old system. That's a valid outcome, not a failure of the code.
4. Only after a family walk-forward validates for a pair, set it in `family_dispatch.py`'s `STRATEGY_FAMILY_BY_PAIR` and add its params to `VALIDATED_PARAMS_BY_PAIR`
5. Wire `family_dispatch.get_signal_for_pair()` into `scanner.py` at the point where it currently calls `strategies.build_market_snapshot()` — pass the existing function in as `legacy_snapshot_builder` so unvalidated pairs keep working exactly as before
6. Fix the Gemini model ID in `dev_console.py`
7. Move the Telegram token into `keys.py`

## What to upload to a new Claude Project

- Entire `T_bot/` folder contents (or at minimum: `scanner.py`, `config.py`, `alerts.py`, `fcs_api.py`, `chat_listener.py`, the whole `strategies/` folder, `api_healthcheck.py`) — **redact real key values from `keys.py` before uploading, variable names only**
- This handoff summary
- The `Tess_Strategy_Structure.pdf` (locked strategy spec)

## Git push checklist (run in your T_bot root)

```bash
git add strategies/ api_healthcheck.py cleanup_tess_folder.py
git status                     # review what's staged before committing
git commit -m "Add 4-family strategy system (technical_analysis, price_action, fibonacci, SMC) + backtest harness + API health check"
git push
```
If this is the first push of the project, confirm `.gitignore` excludes `keys.py`, `.env`, `.tess_diary/`, and `__pycache__/` before pushing — you do not want real API keys in git history.
