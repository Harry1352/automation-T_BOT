# ==============================================================================
# TESS — STRATEGY MODULE (v1.2 - DUAL-SYSTEM CROSSOVER & PULLBACK)
# Pure decision logic: indicators, timeframe rules, and the AI trade-decision
# call. No network calls, no file writes, no order execution — scanner.py
# owns all of that. This file only answers: "given this data, what's the play?"
# ==============================================================================

import json
import numpy as np
import pandas as pd
from config import GEMINI_API_KEY

# ══════════════════════════════════════════
# TIMEFRAME POLICY
# ══════════════════════════════════════════
# Tess only ever looks at three timeframes, each with exactly one job:
#   4h  — trend filter for POSITION trades (long, patient holds)
#   1h  — trend filter for SWING trades — this is the main working timeframe
#   15m — confirmation only. Never the sole basis for a decision. Nothing
#         shorter than 15m is fetched or used — that's considered too noisy
#         to trust for entries.
TIMEFRAMES = [
    ("tf_15m", "15m", 100),
    ("tf_1h",  "1h",  100),
    ("tf_4h",  "4h",  100),
]

# Which timeframe is the authoritative signal source for each trade_type.
PRIMARY_TF_BY_TRADE_TYPE = {
    "swing": "tf_1h",
    "position": "tf_4h",
}

STRATEGY_RULES = """
TIMEFRAME & DUAL-SYSTEM POLICY (follow exactly — do not deviate):
- 4h EMA9/EMA20 relationship is the ONLY trend filter for a "position" trade.
  If trade_type="position", your reasoning MUST cite 4h trend alignment as the primary justification.
- 1h EMA9/EMA20 relationship is the ONLY trend filter for a "swing" trade.
  If trade_type="swing", your reasoning MUST cite 1h trend alignment as the primary justification.
- 15m is CONFIRMATION ONLY — use it to sanity-check the entry candle, never as the primary reason for a trade.
- Dual-System Entry Mechanics:
  * System A (Macro Trend Filter): Bullish Regime (EMA_200 < EMA_20 < EMA_9). Only Longs permitted.
                                   Bearish Regime (EMA_200 > EMA_20 > EMA_9). Only Shorts permitted.
  * System B (Crossover Entries): Long when EMA_9 crosses above EMA_20 and Close > EMA_200.
                                   Short when EMA_9 crosses below EMA_20 and Close < EMA_200.
  * System C (Pullback Entries): Long when in Bullish Regime, Low dips below EMA_9, and Close stays above EMA_20.
                                  Short when in Bearish Regime, High spikes above EMA_9, and Close stays below EMA_20.
- A long/short decision is ONLY valid if the "technical_signal" on the primary timeframe (1h for
  swing, 4h for position) is nonzero and matches your direction (1 = long, -1 = short). If
  technical_signal is 0 ("Hold") on that timeframe, you MUST output action="skip" regardless of
  how the trend otherwise looks.
- Stop Loss (SL) is placed at recent Swing High/Low or exactly 1.5 * ATR away from entry.
- Take Profit (TP) is target-mapped at 3.0 * ATR (1:2 Risk-to-Reward minimum ratio).
"""


# ── Indicators ───────────────────────────────
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

def calc_atr(df, period=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def calc_adx(df, period=14):
    try:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        plus_dm = high.diff()
        minus_dm = low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        hl = high - low
        hc = (high - close.shift()).abs()
        lc = (low - close.shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        return dx.rolling(period).mean()
    except:
        return pd.Series(0, index=df.index)


# ── Core 9/20 EMA Dual-System Strategy Logic ──
def implement_9_20_ema_strategy(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates moving averages, evaluates crossovers/pullbacks, and generates
    exact technical trading signals to guide LLM validation.
    """
    df = df.copy()

    # Calculate indicators
    df["EMA_9"] = ema(df["close"], 9)
    df["EMA_20"] = ema(df["close"], 20)
    df["EMA_200"] = ema(df["close"], 200)
    df["RSI"] = calc_rsi(df["close"])
    df["ATR"] = calc_atr(df)
    df["ADX"] = calc_adx(df)

    # Shift columns to detect crossovers at index t-1 vs t
    df["EMA_9_prev"] = df["EMA_9"].shift(1)
    df["EMA_20_prev"] = df["EMA_20"].shift(1)

    # Define Structural Market Regimes
    df["Macro_Bullish"] = (df["close"] > df["EMA_200"]) & (df["EMA_9"] > df["EMA_20"])
    df["Macro_Bearish"] = (df["close"] < df["EMA_200"]) & (df["EMA_9"] < df["EMA_20"])

    # System B: Evaluate Crossover Signals
    crossover_long = (df["EMA_9_prev"] <= df["EMA_20_prev"]) & (df["EMA_9"] > df["EMA_20"]) & (df["close"] > df["EMA_200"])
    crossover_short = (df["EMA_9_prev"] >= df["EMA_20_prev"]) & (df["EMA_9"] < df["EMA_20"]) & (df["close"] < df["EMA_200"])

    # System C: Evaluate Pullback Signals
    pullback_long = df["Macro_Bullish"] & (df["low"] <= df["EMA_9"]) & (df["close"] >= df["EMA_20"])
    pullback_short = df["Macro_Bearish"] & (df["high"] >= df["EMA_9"]) & (df["close"] <= df["EMA_20"])

    # Unify Signals (1 = Buy, -1 = Sell, 0 = Hold)
    # ADX > 25 filter added after backtest confirmed real edge (46.5% win
    # rate on BTC, 38.2% on ETH, both well above the 33.3% breakeven point).
    adx_confirmed = df["ADX"] > 25
    df["Signal"] = 0
    df.loc[(crossover_long | pullback_long) & adx_confirmed, "Signal"] = 1
    df.loc[(crossover_short | pullback_short) & adx_confirmed, "Signal"] = -1

    # Classify active signal types
    df["Signal_Type"] = "None"
    df.loc[crossover_long, "Signal_Type"] = "Crossover Long"
    df.loc[pullback_long, "Signal_Type"] = "Pullback Long"
    df.loc[crossover_short, "Signal_Type"] = "Crossover Short"
    df.loc[pullback_short, "Signal_Type"] = "Pullback Short"

    # Calculate exact mathematical risk boundaries
    df["Stop_Loss"] = np.nan
    df["Take_Profit"] = np.nan

    df.loc[df["Signal"] == 1, "Stop_Loss"] = df["close"] - (df["ATR"] * 1.5)
    df.loc[df["Signal"] == 1, "Take_Profit"] = df["close"] + (df["ATR"] * 3.0)

    df.loc[df["Signal"] == -1, "Stop_Loss"] = df["close"] + (df["ATR"] * 1.5)
    df.loc[df["Signal"] == -1, "Take_Profit"] = df["close"] - (df["ATR"] * 3.0)

    # Clean intermediate calculation columns
    drop_cols = ["EMA_9_prev", "EMA_20_prev", "Macro_Bullish", "Macro_Bearish"]
    df.drop(columns=drop_cols, inplace=True, errors="ignore")

    return df


# ── Snapshot builder ─────────────────────────
def build_market_snapshot(pair, candles):
    """
    candles: dict like {"tf_15m": df, "tf_1h": df, "tf_4h": df} — already
    fetched by scanner.py's get_candles(). This function does no fetching.
    """
    snapshot = {"pair": pair}
    for label, tf, _ in TIMEFRAMES:
        df = candles.get(label)
        if df is None or len(df) < 30:
            snapshot[label] = None
            continue

        # Execute 9/20 EMA Strategy internally over the timeframe data
        processed = implement_9_20_ema_strategy(df)
        latest_candle = processed.iloc[-1]
        price = float(latest_candle["close"])

        snapshot[label] = {
            "timeframe"          : tf,
            "price"              : round(price, 4),
            "ema9"               : round(float(latest_candle["EMA_9"]), 4) if not pd.isna(latest_candle["EMA_9"]) else None,
            "ema20"              : round(float(latest_candle["EMA_20"]), 4) if not pd.isna(latest_candle["EMA_20"]) else None,
            "ema200"             : round(float(latest_candle["EMA_200"]), 4) if not pd.isna(latest_candle["EMA_200"]) else None,
            "trend_200"          : "above EMA (uptrend)" if price > latest_candle["EMA_200"] else "below EMA (downtrend)",
            "ema_cross"          : "EMA9 above EMA20" if latest_candle["EMA_9"] > latest_candle["EMA_20"] else "EMA9 below EMA20",
            "rsi"                : round(float(latest_candle["RSI"]), 1) if not pd.isna(latest_candle["RSI"]) else None,
            "atr"                : round(float(latest_candle["ATR"]), 4) if not pd.isna(latest_candle["ATR"]) else None,
            "adx_trend_strength" : round(float(latest_candle["ADX"]), 1) if not pd.isna(latest_candle["ADX"]) else None,
            "technical_signal"   : int(latest_candle["Signal"]),
            "signal_type"        : str(latest_candle["Signal_Type"]),
            "suggested_sl"       : round(float(latest_candle["Stop_Loss"]), 4) if not pd.isna(latest_candle["Stop_Loss"]) else None,
            "suggested_tp"       : round(float(latest_candle["Take_Profit"]), 4) if not pd.isna(latest_candle["Take_Profit"]) else None,
        }
    return snapshot


# ── AI decision call ─────────────────────────
def ask_tess(market_snapshots, open_positions, balance, daily_pnl, recent_trades, lessons_context):
    """
    Returns (parsed_decision_dict_or_None, next_planned_trade_string, reasoning_trace).
    scanner.py is responsible for assigning the returned string to its own
    global — this function does not reach into scanner.py's state.
    """
    trade_summary = []
    for t in recent_trades[-15:]:
        trade_summary.append({
            "pair"     : t.get("pair"),
            "direction": t.get("direction"),
            "pnl"      : t.get("pnl"),
            "reason"   : t.get("reason", "")[:60],
        })

    prompt = f"""You are Tess, an autonomous AI trading companion.

{STRATEGY_RULES}

{lessons_context}

══════════════════════════════════════
ACCOUNT STATUS
══════════════════════════════════════
Balance: ${balance:.2f} USD
Today P&L: ${daily_pnl:+.2f}
Open positions: {json.dumps(list(open_positions.keys())) if open_positions else "none"}

══════════════════════════════════════
HISTORICAL SESSIONS
══════════════════════════════════════
{json.dumps(trade_summary, indent=2)}

══════════════════════════════════════
LIVE DATA SNAPSHOT (15m / 1h / 4h only)
══════════════════════════════════════
{json.dumps(market_snapshots, indent=2)}

Think systematically:
1. Decide trade_type FIRST based on what the 4h and 1h trends look like — a
   clean, strong 4h trend supports a "position" call; a clean 1h trend without
   4h confirmation supports a "swing" call; if neither is clean, skip.
2. Verify active "technical_signal" and "signal_type" flags in each timeframe.
   Long/Short actions are only justified if System B (Crossover) or System C (Pullback) are active on the target timeframe.
3. Confirm the entry isn't happening into an obviously bad 15m candle.
4. If past history shows repeated losses on a specific asset today, stand aside.
5. Automatically optimize entry and risk settings based on volatility (ATR).

Output format MUST be raw JSON:
{{
  "decisions": [
    {{
      "pair": "ETH/USDT" or "SOL/USDT" or "BTC/USDT" or "XAUT/USDT",
      "action": "long" or "short" or "skip",
      "trade_type": "swing" or "position",
      "confidence": 0-100,
      "entry_price": number,
      "sl_price": number,
      "tp_price": number,
      "reasoning": "your detailed reasoning, must cite the correct primary timeframe per the policy above"
    }}
  ]
}}"""

    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=GEMINI_API_KEY
        )

        # max_tokens is the TOTAL ceiling for reasoning + answer combined.
        # reasoning_budget caps reasoning specifically so it can never eat
        # the entire pool and leave zero tokens for the actual JSON answer
        # (this is exactly what caused the empty-response / JSONDecodeError
        # failures — the reasoning trace ran long and consumed everything).
        completion = client.chat.completions.create(
            model="nvidia/nemotron-3-ultra-550b-a55b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.25,
            top_p=0.95,
            max_tokens=8192,
            response_format={"type": "json_object"},
            extra_body={
                "chat_template_kwargs": {"enable_thinking": True},
                "reasoning_budget": 5000,
            },
            stream=True
        )

        full_content = ""
        reasoning_trace = ""

        for chunk in completion:
            if not chunk.choices:
                continue
            reasoning = getattr(chunk.choices[0].delta, "reasoning_content", None)
            if reasoning:
                reasoning_trace += reasoning
            content = chunk.choices[0].delta.content
            if content is not None:
                full_content += content

        raw_json = full_content.strip()
        raw_json = raw_json.replace("```json", "").replace("```", "").strip()

        if not raw_json:
            raise ValueError(
                "Nemotron returned an empty response — likely the reasoning trace "
                "consumed the entire token budget, leaving none for the answer."
            )

        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            import re
            decoder = json.JSONDecoder(strict=False)
            repaired = re.sub(r',\s*([}\]])', r'\1', raw_json)
            parsed, _ = decoder.raw_decode(repaired)

        decision = parsed.get("decisions", [{}])[0]

        # ── Code-level enforcement of the System B/C rule ──
        # The prompt asks the model to only go long/short when technical_signal
        # matches on the primary timeframe, but a prompt instruction is not a
        # guarantee. Since technical_signal is deterministic Python output, we
        # can and should verify it here rather than trust compliance blindly —
        # same principle as the SL/TP sanity check already in scanner.py.
        action = decision.get("action")
        pair = decision.get("pair")
        trade_type = decision.get("trade_type", "swing")
        primary_tf = PRIMARY_TF_BY_TRADE_TYPE.get(trade_type, "tf_1h")
        pair_snapshot = market_snapshots.get(pair, {})
        tf_data = pair_snapshot.get(primary_tf) if pair_snapshot else None

        if action in ("long", "short"):
            expected_signal = 1 if action == "long" else -1
            actual_signal = tf_data.get("technical_signal") if tf_data else None

            if actual_signal != expected_signal:
                decision["action"] = "skip"
                decision["reasoning"] = (
                    f"[Overridden by code-level check] Model proposed {action} on {pair} "
                    f"but {primary_tf} technical_signal was {actual_signal}, not {expected_signal}. "
                    f"Original reasoning: {decision.get('reasoning', '')[:150]}"
                )
                action = "skip"

        # ── Code-level enforcement of SL/TP sanity ──
        # suggested_sl / suggested_tp on the primary timeframe are deterministic,
        # ATR-based values computed in Python — trust those over the model's
        # freeform numbers, the same way technical_signal is enforced above.
        # This is what catches (and fixes) the SL == entry_price failure mode.
        if action in ("long", "short") and tf_data:
            min_distance = (tf_data.get("atr") or 0) * 0.5  # sanity floor
            entry = decision.get("entry_price", 0)
            sl = decision.get("sl_price", 0)
            tp = decision.get("tp_price", 0)

            sl_invalid = abs(entry - sl) < min_distance
            wrong_side = (action == "long" and sl >= entry) or (action == "short" and sl <= entry)

            if sl_invalid or wrong_side:
                decision["sl_price"] = tf_data.get("suggested_sl", sl)
                decision["tp_price"] = tf_data.get("suggested_tp", tp)
                decision["reasoning"] = (
                    f"[SL/TP corrected by code-level check — model output was invalid "
                    f"(entry={entry}, sl={sl})] " + decision.get("reasoning", "")[:150]
                )

        if action == "skip":
            next_planned_trade = f"Plan: Standby. Skip: {decision.get('reasoning', '')[:100]}..."
        else:
            next_planned_trade = (
                f"Plan: {decision.get('trade_type','').upper()} {decision.get('pair')} "
                f"{decision.get('action','').upper()} | Confidence: {decision.get('confidence')}%"
            )

        return parsed, next_planned_trade, reasoning_trace

    except Exception as e:
        return None, None, f"Nvidia deep reasoning engine failed: {e}"