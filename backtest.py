"""
backtest.py — deterministic backtest of the 9/20 EMA dual-system strategy
against real historical price data.

Important scope note: this tests ONLY the technical signal layer (System
B/C in strategies.py) — not the full live pipeline, which also involves
Nemotron's judgment (trade_type selection, confidence, "stand aside after
losses"). This answers the right FIRST question: is the underlying
technical edge real at all, before trusting anything built on top of it.
"""

import requests
import pandas as pd
from strategies import implement_9_20_ema_strategy

BINANCE_URL = "https://api.binance.com/api/v3/klines"


def fetch_candles(symbol="BTCUSDT", interval="1h", total_bars=1000):
    """
    Binance caps a single request at 1000 bars — paginate backward in
    time to gather a longer window when total_bars exceeds that.
    """
    all_rows = []
    end_time = None
    remaining = total_bars

    while remaining > 0:
        batch = min(remaining, 1000)
        params = {"symbol": symbol, "interval": interval, "limit": batch}
        if end_time:
            params["endTime"] = end_time
        resp = requests.get(BINANCE_URL, params=params, timeout=20)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        all_rows = rows + all_rows
        end_time = rows[0][0] - 1  # page further back before this batch
        remaining -= len(rows)

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def run_backtest(df, min_adx=0, require_volume=False):
    """
    Walk every signal bar. Enter at that bar's close. Scan forward bar by
    bar until Stop_Loss or Take_Profit is hit using each bar's high/low.
    If a bar touches both in the same candle, assume the worse outcome
    (stop hit first) since intrabar order can't be known from OHLC alone.

    min_adx: if > 0, skip signals where ADX is below this.
    require_volume: if True, skip signals where volume is below its own
    20-bar rolling average — a real, repeatedly-cited confirmation filter,
    being tested here rather than trusted on claim alone.
    """
    processed = implement_9_20_ema_strategy(df)
    processed["vol_ma20"] = processed["volume"].rolling(20).mean()
    trades = []

    for i in range(len(processed) - 1):
        row = processed.iloc[i]
        if row["Signal"] == 0:
            continue
        if min_adx and (pd.isna(row["ADX"]) or row["ADX"] < min_adx):
            continue
        if require_volume and (pd.isna(row["vol_ma20"]) or row["volume"] < row["vol_ma20"]):
            continue

        direction = row["Signal"]
        sl, tp = row["Stop_Loss"], row["Take_Profit"]
        if pd.isna(sl) or pd.isna(tp):
            continue

        outcome = None
        for j in range(i + 1, len(processed)):
            bar = processed.iloc[j]
            if direction == 1:
                hit_sl, hit_tp = bar["low"] <= sl, bar["high"] >= tp
            else:
                hit_sl, hit_tp = bar["high"] >= sl, bar["low"] <= tp

            if hit_sl:
                outcome = "loss"
                break
            elif hit_tp:
                outcome = "win"
                break

        if outcome:
            trades.append({
                "direction": "long" if direction == 1 else "short",
                "outcome": outcome,
                "signal_type": row["Signal_Type"],
            })

    return trades


def summarize(trades):
    if not trades:
        print("No resolved trades in this window.")
        return
    wins = [t for t in trades if t["outcome"] == "win"]
    win_rate = len(wins) / len(trades) * 100
    print(f"Total resolved trades : {len(trades)}")
    print(f"Wins / Losses         : {len(wins)} / {len(trades) - len(wins)}")
    print(f"Win rate              : {win_rate:.1f}%")
    print(f"Breakeven needed      : ~33.3% (fixed 1:2 reward:risk)")


if __name__ == "__main__":
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        print(f"\n{'='*50}\n{symbol} — 1h candles, last ~125 days (3000 bars)\n{'='*50}")
        df = fetch_candles(symbol, "1h", 3000)

        print("\n-- Baseline (no filter) --")
        summarize(run_backtest(df, min_adx=0))

        print("\n-- Volume confirmation only --")
        summarize(run_backtest(df, min_adx=0, require_volume=True))

        print("\n-- ADX > 25 (best threshold found on BTC previously) --")
        summarize(run_backtest(df, min_adx=25))

        print("\n-- ADX > 25 + Volume confirmation combined --")
        summarize(run_backtest(df, min_adx=25, require_volume=True))