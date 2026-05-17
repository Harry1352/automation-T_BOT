# ============================================
# TESS — Final Backtest v3
# Matches her exact live brain
# 9/20/200 EMA strategy
# 5 year historical data
# 20x leverage applied
# Breakeven + trailing stop simulation
# ============================================

import ccxt
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime

# ── Settings — must match scanner.py exactly ─
PAIRS = {
    "ETH/USDT": "1h",
    "SOL/USDT": "1h",
}
STARTING_BALANCE = 16000.0
LEVERAGE         = 20
MAX_RISK_PCT     = 2.0
REWARD_TO_RISK   = 2.5
MIN_CONFIDENCE   = 82
MIN_PROFIT       = 10.0
YEARS_BACK       = 5

exchange = ccxt.binance({"enableRateLimit": True})

# ── Download 5 years of data ──────────────────
def download_data(pair, timeframe, years=5):
    print(f"\n[Tess] Downloading {years} years of "
          f"{pair} {timeframe} data...")
    since       = exchange.parse8601(
        f"{datetime.now().year - years}-01-01T00:00:00Z"
    )
    all_candles = []
    while True:
        candles = exchange.fetch_ohlcv(
            pair, timeframe, since=since, limit=1000
        )
        if not candles:
            break
        all_candles += candles
        since = candles[-1][0] + 1
        if len(candles) < 1000:
            break
    df = pd.DataFrame(
        all_candles,
        columns=["time","open","high","low","close","volume"]
    )
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    df = df.drop_duplicates("time").reset_index(drop=True)
    print(f"[Tess] Downloaded {len(df):,} candles — "
          f"{df['time'].iloc[0].strftime('%Y-%m-%d')} to "
          f"{df['time'].iloc[-1].strftime('%Y-%m-%d')}")
    return df

# ── Indicators ────────────────────────────────
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

def is_sideways(close, i, lookback=20, threshold=0.015):
    if i < lookback:
        return True
    recent = close.iloc[i-lookback:i]
    rng    = (recent.max() - recent.min()) / recent.mean()
    return rng < threshold

def ema_crossed_up(e9, e20, i):
    if i < 1:
        return False
    return e9.iloc[i-1] <= e20.iloc[i-1] and e9.iloc[i] > e20.iloc[i]

def ema_crossed_down(e9, e20, i):
    if i < 1:
        return False
    return e9.iloc[i-1] >= e20.iloc[i-1] and e9.iloc[i] < e20.iloc[i]

# ── Score one candle ──────────────────────────
def score_candle(df, i, e9, e20, e200, rsi_s, atr_s):
    if i < 210:
        return None

    price   = df["close"].iloc[i]
    rsi_now = rsi_s.iloc[i]
    atr_now = atr_s.iloc[i]

    if pd.isna(rsi_now) or pd.isna(atr_now) or atr_now == 0:
        return None

    if is_sideways(df["close"], i):
        return None

    above_200   = price > e200.iloc[i]
    below_200   = price < e200.iloc[i]
    fresh_long  = ema_crossed_up(e9, e20, i) or \
                  (i >= 2 and ema_crossed_up(e9, e20, i-1))
    fresh_short = ema_crossed_down(e9, e20, i) or \
                  (i >= 2 and ema_crossed_down(e9, e20, i-1))

    avg_vol = df["volume"].iloc[max(0,i-20):i].mean()
    vol_ok  = df["volume"].iloc[i] > avg_vol * 1.2

    long_score  = 0
    short_score = 0

    # Trend — 30pts
    if above_200:
        long_score  += 30
    elif below_200:
        short_score += 30

    # EMA cross — 25pts
    if fresh_long:
        long_score  += 25
    elif fresh_short:
        short_score += 25
    else:
        if e9.iloc[i] > e20.iloc[i]:
            long_score  += 10
        else:
            short_score += 10

    # RSI — 20pts
    if rsi_now < 35:
        long_score  += 20
    elif rsi_now > 65:
        short_score += 20
    elif 35 < rsi_now < 65:
        if long_score > short_score:
            long_score  += 12
        else:
            short_score += 12

    # Volume — 15pts
    if vol_ok:
        long_score  += 15
        short_score += 15

    # Block against trend
    if above_200 and short_score > long_score:
        return None
    if below_200 and long_score > short_score:
        return None

    direction  = "long" if long_score >= short_score else "short"
    confidence = long_score if direction == "long" else short_score
    confidence = min(confidence, 95)

    sl_dist = atr_now * 1.2
    tp_dist = sl_dist * REWARD_TO_RISK

    if direction == "long":
        sl = price - sl_dist
        tp = price + tp_dist
    else:
        sl = price + sl_dist
        tp = price - tp_dist

    # Check minimum profit
    risk_amount   = STARTING_BALANCE * (MAX_RISK_PCT / 100)
    risk_per_unit = abs(price - sl)
    if risk_per_unit == 0:
        return None
    size             = (risk_amount / risk_per_unit) * LEVERAGE
    potential_profit = abs(tp - price) * size

    if potential_profit < MIN_PROFIT:
        return None

    return {
        "direction" : direction,
        "confidence": confidence,
        "price"     : price,
        "sl"        : sl,
        "tp"        : tp,
        "size"      : size,
    }

# ── Run backtest ──────────────────────────────
def backtest_pair(pair, timeframe):
    df = download_data(pair, timeframe, YEARS_BACK)

    e9    = ema(df["close"], 9)
    e20   = ema(df["close"], 20)
    e200  = ema(df["close"], 200)
    rsi_s = calc_rsi(df["close"])
    atr_s = calc_atr(df)

    balance        = STARTING_BALANCE
    trades         = []
    in_trade       = False
    entry_price    = sl_price = tp_price = size = 0
    direction      = ""
    entry_time     = None
    breakeven_set  = False
    trailing_set   = False
    consec_losses  = 0
    cooldown_until = 0

    print(f"[Tess] Running 5-year backtest on {pair}...")

    for i in range(210, len(df)):
        price = df["close"].iloc[i]
        ts    = df["time"].iloc[i].timestamp()

        # Cooldown check
        if cooldown_until and ts < cooldown_until:
            continue

        if in_trade:
            current_sl = sl_price

            # Breakeven — once $10 profit
            if not breakeven_set:
                unreal = (price-entry_price)*size if direction=="long" \
                         else (entry_price-price)*size
                if unreal >= MIN_PROFIT:
                    current_sl    = entry_price
                    breakeven_set = True

            # Trailing — once $20 profit
            if breakeven_set and not trailing_set:
                unreal = (price-entry_price)*size if direction=="long" \
                         else (entry_price-price)*size
                if unreal >= MIN_PROFIT * 2:
                    if direction == "long":
                        current_sl = price - (price-entry_price)*0.4
                    else:
                        current_sl = price + (entry_price-price)*0.4
                    trailing_set = True

            hit_sl = ((direction=="long"  and price <= current_sl) or
                      (direction=="short" and price >= current_sl))
            hit_tp = ((direction=="long"  and price >= tp_price) or
                      (direction=="short" and price <= tp_price))

            if hit_sl or hit_tp:
                pnl = (price-entry_price)*size if direction=="long" \
                      else (entry_price-price)*size
                pnl      = round(pnl, 2)
                balance += pnl

                trades.append({
                    "pair"      : pair,
                    "direction" : direction,
                    "entry"     : entry_price,
                    "exit"      : round(price, 4),
                    "sl"        : sl_price,
                    "tp"        : tp_price,
                    "pnl"       : pnl,
                    "result"    : "win" if pnl > 0 else "loss",
                    "exit_type" : "tp" if hit_tp else "sl",
                    "entry_time": str(entry_time),
                    "exit_time" : str(df["time"].iloc[i]),
                    "balance"   : round(balance, 2),
                })

                if pnl < 0:
                    consec_losses += 1
                    if consec_losses >= 2:
                        cooldown_until = ts + (4 * 3600)
                        consec_losses  = 0
                else:
                    consec_losses = 0

                in_trade = False

        else:
            setup = score_candle(df, i, e9, e20, e200, rsi_s, atr_s)
            if setup and setup["confidence"] >= MIN_CONFIDENCE:
                entry_price   = setup["price"]
                sl_price      = setup["sl"]
                tp_price      = setup["tp"]
                size          = setup["size"]
                direction     = setup["direction"]
                entry_time    = df["time"].iloc[i]
                in_trade      = True
                breakeven_set = False
                trailing_set  = False

    return trades, balance

# ── Print report ──────────────────────────────
def print_report(pair, timeframe, trades, final_balance):
    if not trades:
        print(f"\n[Tess] No trades for {pair} — "
              f"try adjusting MIN_CONFIDENCE")
        return

    df        = pd.DataFrame(trades)
    wins      = df[df["result"] == "win"]
    losses    = df[df["result"] == "loss"]
    tp_hits   = df[df["exit_type"] == "tp"]
    sl_hits   = df[df["exit_type"] == "sl"]
    win_rate  = round(len(wins)/len(df)*100, 1)
    total_pnl = round(final_balance - STARTING_BALANCE, 2)
    avg_win   = round(wins["pnl"].mean(), 2)   if len(wins)   > 0 else 0
    avg_loss  = round(losses["pnl"].mean(), 2) if len(losses) > 0 else 0
    best      = round(df["pnl"].max(), 2)
    worst     = round(df["pnl"].min(), 2)

    # Max drawdown
    peak   = STARTING_BALANCE
    max_dd = 0
    for b in df["balance"]:
        peak   = max(peak, b)
        dd     = round((peak - b) / peak * 100, 2)
        max_dd = max(max_dd, dd)

    # Max consecutive losses
    max_cl = cur_cl = 0
    for r in df["result"]:
        cur_cl = cur_cl+1 if r=="loss" else 0
        max_cl = max(max_cl, cur_cl)

    # Monthly breakdown
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df["month"]     = df["exit_time"].dt.to_period("M")
    monthly         = df.groupby("month")["pnl"].sum().round(2)
    profitable_months = len(monthly[monthly > 0])
    total_months      = len(monthly)

    print(f"""
{'='*56}
  TESS FINAL BACKTEST — {pair}
  {YEARS_BACK} years · {timeframe} candles · 20x leverage
  9/20/200 EMA · Breakeven + Trailing · Cooldown
{'='*56}

  TRADES
  Total trades         {len(df)}
  Wins                 {len(wins)}
  Losses               {len(losses)}
  Win rate             {win_rate}%
  TP hits              {len(tp_hits)}
  SL hits              {len(sl_hits)}

  MONEY
  Starting balance     ${STARTING_BALANCE:,.2f}
  Final balance        ${final_balance:,.2f}
  Total profit         {'+'if total_pnl>=0 else ''}{total_pnl:,.2f} USD
  Average win          +${avg_win}
  Average loss         -${abs(avg_loss)}
  Best single trade    +${best}
  Worst single trade   ${worst}

  RISK
  Max drawdown         {max_dd}%
  Max losing streak    {max_cl} trades in a row
  Profitable months    {profitable_months} out of {total_months}

{'='*56}
""")

    # Monthly P&L table
    print("  Monthly P&L breakdown:")
    print("  " + "-"*30)
    for month, pnl in monthly.items():
        bar = "+" * int(abs(pnl)/50) if pnl > 0 else "-" * int(abs(pnl)/50)
        bar = bar[:20]
        sign = "+" if pnl >= 0 else ""
        print(f"  {month}   {sign}${pnl:>8.2f}  {bar}")
    print("  " + "-"*30)

    # Save files
    fname = f"backtest_final_{pair.replace('/','_')}.json"
    result = {
        "pair": pair, "timeframe": timeframe,
        "years": YEARS_BACK, "leverage": LEVERAGE,
        "total_trades": len(df),
        "wins": len(wins), "losses": len(losses),
        "win_rate": win_rate,
        "starting_balance": STARTING_BALANCE,
        "final_balance": round(final_balance, 2),
        "total_pnl": total_pnl,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "best_trade": best, "worst_trade": worst,
        "max_drawdown_pct": max_dd,
        "max_losing_streak": max_cl,
        "profitable_months": profitable_months,
        "total_months": total_months,
    }
    with open(fname, "w") as f:
        json.dump(result, f, indent=2)
    df.to_csv(
        f"backtest_final_{pair.replace('/','_')}_trades.csv",
        index=False
    )
    print(f"\n  Saved to {fname}")
    print(f"  All trades in backtest_final_"
          f"{pair.replace('/','_')}_trades.csv\n")

# ── Main ──────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*56)
    print("  TESS — 5 Year Final Backtest")
    print(f"  Strategy : 9/20/200 EMA")
    print(f"  Leverage : {LEVERAGE}x")
    print(f"  Balance  : ${STARTING_BALANCE:,.2f}")
    print(f"  Years    : {YEARS_BACK}")
    print("="*56)

    for pair, tf in PAIRS.items():
        trades, final = backtest_pair(pair, tf)
        print_report(pair, tf, trades, final)

    print("\n  All done. These are Tess's real")
    print("  historical numbers before she touches")
    print("  your money.\n")
    