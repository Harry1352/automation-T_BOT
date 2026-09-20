# ==============================================================================
# TESS WORKS — Calibrated Historical Backtesting Engine (backtester_v2.py)
# ==============================================================================

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime

# --- Calibrated Backtest Settings ---
SYMBOL = "SOL/USDT"
TIMEFRAME = "15m"
LIMIT = 1000                  
MIN_ADX_THRESHOLD = 14.0       
ATR_MULTIPLIER = 2.5          # Increased from 1.2 to allow positions to breathe
REWARD_TO_RISK = 2.0          # Adjusted to 2.0x for realistic trending targets
COOLDOWN_CANDLES = 6          # 1.5 hours cooldown after trade close to prevent over-trading

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_indicators(df):
    df['ema9'] = ema(df['close'], 9)
    df['ema20'] = ema(df['close'], 20)
    df['ema200'] = ema(df['close'], 200)
    
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low'] - df['close'].shift()).abs()
    df['tr'] = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['atr'] = df['tr'].rolling(14).mean()
    
    plus_dm = df['high'].diff()
    minus_dm = df['low'].diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    
    plus_di = 100 * (plus_dm.rolling(14).mean() / df['atr'])
    minus_di = 100 * (minus_dm.rolling(14).mean() / df['atr'])
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    df['adx'] = dx.rolling(14).mean()
    
    return df

def fetch_historical_data(symbol, timeframe, limit):
    print(f"Fetching historical data for {symbol} ({timeframe})...")
    try:
        exchange = ccxt.binance({"enableRateLimit": True})
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as e:
        print(f"Primary fetch failed: {e}. Trying fallback exchange...")
        exchange = ccxt.kucoin({"enableRateLimit": True})
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

def run_backtest():
    df = fetch_historical_data(SYMBOL, TIMEFRAME, LIMIT)
    df = calculate_indicators(df)
    
    print("Beginning strategy simulation over historical candles...")
    
    trades = []
    active_trade = None
    cooldown_until_idx = 0
    
    for i in range(200, len(df)):
        current_candle = df.iloc[i]
        
        # 1. Manage Active Trade
        if active_trade:
            if current_candle['low'] <= active_trade['sl']:
                trades.append({
                    "entry_time": active_trade["entry_time"],
                    "exit_time": current_candle["time"],
                    "direction": active_trade["direction"],
                    "entry_price": active_trade["entry_price"],
                    "exit_price": active_trade['sl'],
                    "pnl": -active_trade["risk_usd"],
                    "result": "LOSS"
                })
                active_trade = None
                cooldown_until_idx = i + COOLDOWN_CANDLES  # Start cooldown timer
            elif current_candle['high'] >= active_trade['tp']:
                trades.append({
                    "entry_time": active_trade["entry_time"],
                    "exit_time": current_candle["time"],
                    "direction": active_trade["direction"],
                    "entry_price": active_trade["entry_price"],
                    "exit_price": active_trade['tp'],
                    "pnl": active_trade["risk_usd"] * REWARD_TO_RISK,
                    "result": "WIN"
                })
                active_trade = None
                cooldown_until_idx = i + COOLDOWN_CANDLES  # Start cooldown timer
            continue
            
        # 2. Check Cooldown Gate
        if i < cooldown_until_idx:
            continue
            
        # 3. Check Trend Filters
        ema9 = current_candle['ema9']
        ema20 = current_candle['ema20']
        ema200 = current_candle['ema200']
        adx = current_candle['adx']
        close_price = current_candle['close']
        atr = current_candle['atr']
        
        direction = None
        if ema9 > ema20 and close_price > ema200 and adx > MIN_ADX_THRESHOLD:
            direction = "long"
        elif ema9 < ema20 and close_price < ema200 and adx > MIN_ADX_THRESHOLD:
            direction = "short"
            
        # 4. Simulate Entry
        if direction:
            sl_dist = atr * ATR_MULTIPLIER
            sl = round(close_price - sl_dist, 4) if direction == "long" else round(close_price + sl_dist, 4)
            tp_dist = sl_dist * REWARD_TO_RISK
            tp = round(close_price + tp_dist, 4) if direction == "long" else round(close_price - tp_dist, 4)
            
            active_trade = {
                "entry_time": current_candle["time"],
                "direction": direction,
                "entry_price": close_price,
                "sl": sl,
                "tp": tp,
                "risk_usd": 3.60
            }

    if not trades:
        print("\n[Result] Strategy executed 0 trades.")
        return

    results_df = pd.DataFrame(trades)
    total_trades = len(results_df)
    wins = len(results_df[results_df['result'] == "WIN"])
    losses = len(results_df[results_df['result'] == "LOSS"])
    win_rate = (wins / total_trades) * 100
    total_pnl = results_df['pnl'].sum()
    
    print("\n" + "="*50)
    print(f" TESS FCS CALIBRATED BACKTEST REPORT")
    print("="*50)
    print(f"Total Simulated:     {LIMIT} candles")
    print(f"Total Executed:      {total_trades} trades (Controlled over-trading)")
    print(f"Wins:                {wins}")
    print(f"Losses:              {losses}")
    print(f"Win Rate:            {win_rate:.1f}%")
    print(f"Simulated P&L:       {total_pnl:+.2f} USD")
    print("="*50)

if __name__ == "__main__":
    run_backtest()