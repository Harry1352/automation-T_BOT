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
CANDLE_LIMIT = 1000            # Safe local data limit per run
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
def run_ema_strategy(df, min_adx_threshold=14.0):
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

    df["Signal"] = 0
    df.loc[crossover_long | pullback_long, "Signal"] = 1
    df.loc[crossover_short | pullback_short, "Signal"] = -1
    
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

# --- Data Fetching with Graceful Fallbacks ---
def fetch_data(symbol, timeframe, limit):
    try:
        exchange = ccxt.binance({"enableRateLimit": True})
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception:
        try:
            exchange = ccxt.kucoin({"enableRateLimit": True})
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        except Exception as e:
            # Return None to let the loop bypass gracefully instead of crashing
            return None
        
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
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
                trades.append({"result": "LOSS", "pnl": pnl})
                active_trade = None
                cooldown_until = i + 6
            elif current_candle['high'] >= active_trade['sl'] and active_trade['direction'] == "short":
                pnl = -active_trade['margin_usd'] * (active_trade['sl_dist'] / active_trade['entry_price']) * LEVERAGE
                pnl -= active_trade['margin_usd'] * LEVERAGE * TAKER_FEE_PCT * 2
                balance += pnl
                trades.append({"result": "LOSS", "pnl": pnl})
                active_trade = None
                cooldown_until = i + 6
                
            # Check Take Profit
            elif current_candle['high'] >= active_trade['tp'] and active_trade['direction'] == "long":
                pnl = active_trade['margin_usd'] * (active_trade['tp_dist'] / active_trade['entry_price']) * LEVERAGE
                pnl -= active_trade['margin_usd'] * LEVERAGE * TAKER_FEE_PCT * 2
                balance += pnl
                trades.append({"result": "WIN", "pnl": pnl})
                active_trade = None
                cooldown_until = i + 6
            elif current_candle['low'] <= active_trade['tp'] and active_trade['direction'] == "short":
                pnl = active_trade['margin_usd'] * (active_trade['tp_dist'] / active_trade['entry_price']) * LEVERAGE
                pnl -= active_trade['margin_usd'] * LEVERAGE * TAKER_FEE_PCT * 2
                balance += pnl
                trades.append({"result": "WIN", "pnl": pnl})
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
                "tp_dist": tp_dist,
                "margin_usd": margin_usd
            }
            
    if not trades:
        return 0, 0, 0, 0.0, starting_balance
        
    trades_df = pd.DataFrame(trades)
    total = len(trades_df)
    wins = len(trades_df[trades_df['result'] == "WIN"])
    losses = len(trades_df[trades_df['result'] == "LOSS"])
    winrate = (wins / total) * 100 if total > 0 else 0
    
    return total, wins, losses, winrate, balance

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
            
            # Process both strategies over identical data
            df_ema = run_ema_strategy(raw_df)
            df_bollinger = run_bollinger_strategy(raw_df)
            
            # Execute Simulations
            t_ema, w_ema, l_ema, wr_ema, bal_ema = simulate_strategy(df_ema, "9/20 EMA Strategy")
            t_bb, w_bb, l_bb, wr_bb, bal_bb = simulate_strategy(df_bollinger, "Bollinger Bands Reversion")
            
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
            print("-"*70)
            print(" STRATEGY B: Bollinger Bands (Mean Reversion)")
            print(f"  - Total Trades:    {t_bb}")
            print(f"  - Win Rate:        {wr_bb:.1f}% ({w_bb}W / {l_bb}L)")
            print(f"  - Ending Balance:  ${bal_bb:,.2f} USD (P&L: {bal_bb-180:+.2f})")
            print("="*70)
            time.sleep(1) # Polite pause to prevent IP rate-limiting

if __name__ == "__main__":
    run_comparative_backtest()