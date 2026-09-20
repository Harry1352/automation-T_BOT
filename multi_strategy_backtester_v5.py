# ==============================================================================
# TESS WORKS — Comparative Multi-Strategy Matrix Backtester (v1.1)
# Run in terminal: python3 multi_strategy_backtester.py
# ==============================================================================

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime
import time

# --- Multi-Matrix Backtest Settings ---
# You can input multiple comma-separated symbols and timeframes directly
SYMBOL = "SOL/USDT, BTC/USDT, ETH/USDT, XAUT/USDT"
TIMEFRAME = "15m, 1h"          # 15m and 1h are highly recommended. (4h contains too few data points in 1000 candles)
CANDLE_LIMIT = 20000           # Now paginated (see fetch_data) -- ~208 days on 15m,
                                # ~2.3 years on 1h. Bump higher for a longer window,
                                # but each 1000-bar page is a separate rate-limited
                                # API call, so 20000 already means dozens of requests
                                # per symbol/timeframe -- expect this run to take a
                                # few minutes, not seconds.
LEVERAGE = 5                  # Standard risk leverage
CAPITAL_ALLOCATION_PCT = 0.12 # Deploying 12% of capital as margin per trade
TAKER_FEE_PCT = 0.0005        # 0.05% Exchange Taker Fee

# --- Mathematical Helpers ---
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs)) if rs is not None else 50.0

def calc_atr(df, period=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
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
        hc = (high - close.shift(1)).abs()
        lc = (low - close.shift(1)).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        return dx.rolling(period).mean()
    except:
        return pd.Series(0, index=df.index)

# --- Strategy A: 9/20 EMA Dual-System (Trend Following) ---
def run_ema_strategy(df, min_adx_threshold=25.0):
    # FIX: min_adx_threshold used to be accepted but never actually applied
    # anywhere below -- this strategy was running with NO ADX filter at all,
    # unlike the live strategies.py which gates on ADX > 25. Default changed
    # to 25 (not config.py's 14.0, which we separately confirmed is dead
    # code and not what's actually live) so this test matches reality.
    df = df.copy()
    df["EMA_9"] = ema(df["close"], 9)
    df["EMA_20"] = ema(df["close"], 20)
    df["EMA_200"] = ema(df["close"], 200)
    df["ATR"] = calc_atr(df)
    df["ADX"] = calc_adx(df)
    
    df["EMA_9_prev"] = df["EMA_9"].shift(1)
    df["EMA_20_prev"] = df["EMA_20"].shift(1)
    
    df["Macro_Bullish"] = (df["close"] > df["EMA_200"]) & (df["EMA_9"] > df["EMA_20"])
    df["Macro_Bearish"] = (df["close"] < df["EMA_200"]) & (df["EMA_9"] < df["EMA_20"])

    crossover_long = (df["EMA_9_prev"] <= df["EMA_20_prev"]) & (df["EMA_9"] > df["EMA_20"]) & (df["close"] > df["EMA_200"])
    crossover_short = (df["EMA_9_prev"] >= df["EMA_20_prev"]) & (df["EMA_9"] < df["EMA_20"]) & (df["close"] < df["EMA_200"])

    pullback_long = df["Macro_Bullish"] & (df["low"] <= df["EMA_9"]) & (df["close"] >= df["EMA_20"])
    pullback_short = df["Macro_Bearish"] & (df["high"] >= df["EMA_9"]) & (df["close"] <= df["EMA_20"])

    adx_confirmed = df["ADX"] > min_adx_threshold

    df["Signal"] = 0
    df.loc[(crossover_long | pullback_long) & adx_confirmed, "Signal"] = 1
    df.loc[(crossover_short | pullback_short) & adx_confirmed, "Signal"] = -1
    
    df["SL_Distance"] = df["ATR"] * 2.5
    df["TP_Distance"] = df["SL_Distance"] * 3.5
    
    return df

# --- Strategy B: Bollinger Bands Mean-Reversion ---
def run_bollinger_strategy(df):
    df = df.copy()
    df["Basis"] = df["close"].rolling(window=20).mean()
    df["Std_Dev"] = df["close"].rolling(window=20).std()
    df["Upper_Band"] = df["Basis"] + (df["Std_Dev"] * 2.0)
    df["Lower_Band"] = df["Basis"] - (df["Std_Dev"] * 2.0)
    df["ATR"] = calc_atr(df)
    
    df["close_prev"] = df["close"].shift(1)
    
    long_entry = (df["close_prev"] >= df["Lower_Band"].shift(1)) & (df["close"] < df["Lower_Band"])
    short_entry = (df["close_prev"] <= df["Upper_Band"].shift(1)) & (df["close"] > df["Upper_Band"])
    
    df["Signal"] = 0
    df.loc[long_entry, "Signal"] = 1
    df.loc[short_entry, "Signal"] = -1
    
    df["SL_Distance"] = df["ATR"] * 1.5
    df["TP_Distance"] = (df["close"] - df["Basis"]).abs()
    
    return df

# --- Strategy C: 9/20 EMA with Rejection-Candle Entries + Chop-Zone Filter ---
# Rules sourced from a retail trader's 9/20 EMA video transcript (Aug 2026),
# translated into testable code:
#   1. Don't enter on first EMA touch -- wait for a REJECTION (price tests the
#      EMA, fails to close through it, prints a candle against the trend),
#      then enter only when price breaks that rejection candle's high/low.
#   2. Stop-loss at the recent swing high/low, not a flat ATR multiple.
#   3. If EMA9/EMA20 have crossed >=3 times in the last 10 candles, treat it
#      as a chop/trap zone -- require price to break the recent 20-candle
#      range before allowing any entry there.
#   4. If the take-profit lands within 0.3% of a round psychological price
#      level, pull it back slightly rather than aiming at the round number
#      itself (claimed tendency for price to stall just short of round
#      numbers). This is the one rule with zero prior evidence behind it --
#      treat the backtest comparison below as the actual test of whether it
#      helps, hurts, or does nothing.
def adjust_target_for_round_numbers(price, raw_target, direction, pullback_pct=0.003):
    if price <= 0:
        return raw_target
    magnitude = 10 ** (len(str(int(price))) - 1)
    round_level = round(raw_target / magnitude) * magnitude
    if round_level == 0 or abs(raw_target - round_level) / price > pullback_pct:
        return raw_target
    buffer = price * pullback_pct
    return round_level - buffer if direction == "long" else round_level + buffer


def run_ema_rejection_strategy(df, chop_window=10, chop_threshold=3, rejection_lookback=5,
                                swing_window=10, base_rr=3.5, apply_round_number_rule=True):
    # FIX: base_rr was 2.5, while Strategy A and live strategies.py both use
    # 3.5. That mismatch alone explained the SOL/1h and XAUT/15m losses --
    # both had win rates above the 3.5x breakeven line (22.2%) but below
    # the 2.5x line (28.6%). Matched to 3.5 now for a fair comparison.
    df = df.copy()
    df["EMA_9"] = ema(df["close"], 9)
    df["EMA_20"] = ema(df["close"], 20)
    df["EMA_200"] = ema(df["close"], 200)

    df["Macro_Bullish"] = (df["close"] > df["EMA_200"]) & (df["EMA_9"] > df["EMA_20"])
    df["Macro_Bearish"] = (df["close"] < df["EMA_200"]) & (df["EMA_9"] < df["EMA_20"])

    # Rejection candles: price tests the EMA, fails to close through it,
    # closes against the prevailing direction.
    rej_short = df["Macro_Bearish"] & (df["high"] >= df["EMA_9"]) & (df["close"] < df["EMA_20"]) & (df["close"] < df["open"])
    rej_long  = df["Macro_Bullish"] & (df["low"]  <= df["EMA_9"]) & (df["close"] > df["EMA_20"]) & (df["close"] > df["open"])

    pending_short_low  = df["low"].where(rej_short).ffill(limit=rejection_lookback)
    pending_long_high  = df["high"].where(rej_long).ffill(limit=rejection_lookback)

    entry_short = df["Macro_Bearish"] & (df["close"] < pending_short_low) & (~rej_short)
    entry_long  = df["Macro_Bullish"] & (df["close"] > pending_long_high) & (~rej_long)

    # Chop-zone filter: too many EMA crossings recently -> require a real
    # range breakout, not just a rejection bounce, before trusting the signal.
    ema_diff_sign = np.sign(df["EMA_9"] - df["EMA_20"])
    crossings = (ema_diff_sign != ema_diff_sign.shift(1)).astype(int).rolling(chop_window).sum()
    chop_zone = crossings >= chop_threshold
    range_high = df["high"].rolling(20).max().shift(1)
    range_low  = df["low"].rolling(20).min().shift(1)

    entry_short = entry_short & (~chop_zone | (df["close"] < range_low))
    entry_long  = entry_long & (~chop_zone | (df["close"] > range_high))

    df["Signal"] = 0
    df.loc[entry_long, "Signal"] = 1
    df.loc[entry_short, "Signal"] = -1

    swing_high = df["high"].rolling(swing_window).max()
    swing_low  = df["low"].rolling(swing_window).min()
    df["SL_Distance"] = np.where(
        df["Signal"] == 1, df["close"] - swing_low,
        np.where(df["Signal"] == -1, swing_high - df["close"], np.nan)
    )

    def _tp_dist(row):
        if row["Signal"] == 0 or pd.isna(row["SL_Distance"]) or row["SL_Distance"] <= 0:
            return np.nan
        direction = "long" if row["Signal"] == 1 else "short"
        raw_target = row["close"] + row["SL_Distance"] * base_rr if direction == "long" \
            else row["close"] - row["SL_Distance"] * base_rr
        if apply_round_number_rule:
            raw_target = adjust_target_for_round_numbers(row["close"], raw_target, direction)
        return abs(raw_target - row["close"])

    df["TP_Distance"] = df.apply(_tp_dist, axis=1)
    return df



def fetch_data(symbol, timeframe, total_bars):
    """
    A single ccxt call caps out at ~1000 bars -- paginate backward in time
    to gather a genuinely multi-year window instead of just the most
    recent snapshot (which was all the previous version fetched).
    """
    def _paginate(exchange):
        all_rows = []
        end_time = None
        remaining = total_bars
        while remaining > 0:
            batch = min(remaining, 1000)
            if end_time:
                rows = exchange.fetch_ohlcv(symbol, timeframe, limit=batch, params={"until": end_time})
            else:
                rows = exchange.fetch_ohlcv(symbol, timeframe, limit=batch)
            if not rows:
                break
            all_rows = rows + all_rows
            end_time = rows[0][0] - 1
            remaining -= len(rows)
            if len(rows) < batch:
                break  # exchange ran out of history for this pair
        return all_rows

    try:
        exchange = ccxt.binance({"enableRateLimit": True})
        ohlcv = _paginate(exchange)
    except Exception:
        try:
            exchange = ccxt.kucoin({"enableRateLimit": True})
            ohlcv = _paginate(exchange)
        except Exception:
            return None

    if not ohlcv:
        return None
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("timestamp").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

# --- Simulation Engine ---
def simulate_strategy(df, name):
    if df is None:
        return 0, 0, 0, 0.0, 180.0
        
    trades = []
    active_trade = None
    cooldown_until = 0
    starting_balance = 180.0
    balance = starting_balance
    
    for i in range(50, len(df)):
        current_candle = df.iloc[i]
        
        # 1. Manage Active Positions
        if active_trade:
            # Check Stop Loss
            if current_candle['low'] <= active_trade['sl'] and active_trade['direction'] == "long":
                pnl = -active_trade['margin_usd'] * (active_trade['sl_dist'] / active_trade['entry_price']) * LEVERAGE
                pnl -= active_trade['margin_usd'] * LEVERAGE * TAKER_FEE_PCT * 2
                balance += pnl
                trades.append({"result": "LOSS", "pnl": pnl, "sl_dist_pct": active_trade["sl_dist_pct"]})
                active_trade = None
                cooldown_until = i + 6
            elif current_candle['high'] >= active_trade['sl'] and active_trade['direction'] == "short":
                pnl = -active_trade['margin_usd'] * (active_trade['sl_dist'] / active_trade['entry_price']) * LEVERAGE
                pnl -= active_trade['margin_usd'] * LEVERAGE * TAKER_FEE_PCT * 2
                balance += pnl
                trades.append({"result": "LOSS", "pnl": pnl, "sl_dist_pct": active_trade["sl_dist_pct"]})
                active_trade = None
                cooldown_until = i + 6
                
            # Check Take Profit
            elif current_candle['high'] >= active_trade['tp'] and active_trade['direction'] == "long":
                pnl = active_trade['margin_usd'] * (active_trade['tp_dist'] / active_trade['entry_price']) * LEVERAGE
                pnl -= active_trade['margin_usd'] * LEVERAGE * TAKER_FEE_PCT * 2
                balance += pnl
                trades.append({"result": "WIN", "pnl": pnl, "sl_dist_pct": active_trade["sl_dist_pct"]})
                active_trade = None
                cooldown_until = i + 6
            elif current_candle['low'] <= active_trade['tp'] and active_trade['direction'] == "short":
                pnl = active_trade['margin_usd'] * (active_trade['tp_dist'] / active_trade['entry_price']) * LEVERAGE
                pnl -= active_trade['margin_usd'] * LEVERAGE * TAKER_FEE_PCT * 2
                balance += pnl
                trades.append({"result": "WIN", "pnl": pnl, "sl_dist_pct": active_trade["sl_dist_pct"]})
                active_trade = None
                cooldown_until = i + 6
            continue
            
        if i < cooldown_until:
            continue
            
        # 2. Check Strategy Entry Signal
        signal = current_candle["Signal"]
        if signal != 0:
            direction = "long" if signal == 1 else "short"
            price = current_candle["close"]
            sl_dist = current_candle["SL_Distance"]
            tp_dist = current_candle["TP_Distance"]
            
            if sl_dist <= 0 or pd.isna(sl_dist) or tp_dist <= 0 or pd.isna(tp_dist):
                continue
                
            sl = price - sl_dist if direction == "long" else price + sl_dist
            tp = price + tp_dist if direction == "long" else price - tp_dist
            
            margin_usd = balance * CAPITAL_ALLOCATION_PCT
            
            active_trade = {
                "direction": direction,
                "entry_price": price,
                "sl": sl,
                "tp": tp,
                "sl_dist": sl_dist,
                "sl_dist_pct": sl_dist / price * 100,  # DIAGNOSTIC: stop width as % of price
                "tp_dist": tp_dist,
                "margin_usd": margin_usd
            }
            
    if not trades:
        return 0, 0, 0, 0.0, starting_balance, 0.0
        
    trades_df = pd.DataFrame(trades)
    total = len(trades_df)
    wins = len(trades_df[trades_df['result'] == "WIN"])
    losses = len(trades_df[trades_df['result'] == "LOSS"])
    winrate = (wins / total) * 100 if total > 0 else 0
    avg_sl_pct = trades_df['sl_dist_pct'].mean() if 'sl_dist_pct' in trades_df.columns else 0.0
    
    return total, wins, losses, winrate, balance, avg_sl_pct

# --- Main Comparative Matrix Runner ---
def run_comparative_backtest():
    # Parse inputs cleanly
    symbols_list = [s.strip().upper() for s in SYMBOL.split(",") if s.strip()]
    timeframes_list = [t.strip().lower() for t in TIMEFRAME.split(",") if t.strip()]

    print(f"Initializing Backtest Matrix: {len(symbols_list)} symbols x {len(timeframes_list)} timeframes.")
    
    for symbol in symbols_list:
        for timeframe in timeframes_list:
            raw_df = fetch_data(symbol, timeframe, CANDLE_LIMIT)
            
            if raw_df is None or len(raw_df) < 250:
                print(f" ⚠️  Skipping {symbol} ({timeframe}): Unsupported or insufficient data on exchange.")
                continue
            
            # Process all three strategies over identical data
            df_ema = run_ema_strategy(raw_df)
            df_bollinger = run_bollinger_strategy(raw_df)
            df_rejection = run_ema_rejection_strategy(raw_df)

            # Execute Simulations
            t_ema, w_ema, l_ema, wr_ema, bal_ema, sl_ema = simulate_strategy(df_ema, "9/20 EMA Strategy")
            t_bb, w_bb, l_bb, wr_bb, bal_bb, sl_bb = simulate_strategy(df_bollinger, "Bollinger Bands Reversion")
            t_rj, w_rj, l_rj, wr_rj, bal_rj, sl_rj = simulate_strategy(df_rejection, "9/20 EMA Rejection+Chop Filter")
            
            print("\n" + "="*70)
            print(f" PERFORMANCE MATRIX ELEMENT: {symbol} ({timeframe})")
            print("="*70)
            print(f"Historical Window:   {raw_df['time'].min()} to {raw_df['time'].max()}")
            print(f"Total Candlesticks:  {len(raw_df)}")
            print(f"Capital Allocation:  {CAPITAL_ALLOCATION_PCT*100}% Margin per trade @ {LEVERAGE}x")
            print(f"Starting Balance:    $180.00")
            print("-"*70)
            print(" STRATEGY A: 9/20 EMA Dual-System (Trend Following)")
            print(f"  - Total Trades:    {t_ema}")
            print(f"  - Win Rate:        {wr_ema:.1f}% ({w_ema}W / {l_ema}L)")
            print(f"  - Ending Balance:  ${bal_ema:,.2f} USD (P&L: {bal_ema-180:+.2f})")
            print(f"  - Avg Stop Width:  {sl_ema:.2f}% of price")
            print("-"*70)
            print(" STRATEGY B: Bollinger Bands (Mean Reversion)")
            print(f"  - Total Trades:    {t_bb}")
            print(f"  - Win Rate:        {wr_bb:.1f}% ({w_bb}W / {l_bb}L)")
            print(f"  - Ending Balance:  ${bal_bb:,.2f} USD (P&L: {bal_bb-180:+.2f})")
            print(f"  - Avg Stop Width:  {sl_bb:.2f}% of price")
            print("-"*70)
            print(" STRATEGY C: 9/20 EMA Rejection-Candle + Chop Filter (from video transcript)")
            print(f"  - Total Trades:    {t_rj}")
            print(f"  - Win Rate:        {wr_rj:.1f}% ({w_rj}W / {l_rj}L)")
            print(f"  - Ending Balance:  ${bal_rj:,.2f} USD (P&L: {bal_rj-180:+.2f})")
            print(f"  - Avg Stop Width:  {sl_rj:.2f}% of price")
            print("="*70)
            time.sleep(1) # Polite pause to prevent IP rate-limiting

if __name__ == "__main__":
    run_comparative_backtest()