# ============================================
# TESS — AI Brain
# Powered by Groq LLaMA 3
# This is where Tess actually THINKS
# ============================================

import requests
import json
from config import GROQ_API_KEY

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def think(market_data, trade_history, balance, open_positions):
    """
    Feed market data to Groq LLaMA 3.
    Tess thinks like an experienced trader.
    Returns a structured trading decision.
    """

    # Build the market context
    context = f"""
You are Tess, an expert cryptocurrency futures trader.
You trade ETH/USDT and SOL/USDT on Delta Exchange India.
You use 20x leverage. You are disciplined, precise, and protect capital above all.

YOUR RULES — never break these:
- Maximum 15% of balance per trade
- Stop loss always set before entry
- Daily loss limit: $9 total
- Never trade against the major trend (EMA 200)
- Scalp trades must exit under 14 minutes (zero fees on Delta)
- Swing trades trail profit and hold as long as trend continues

CURRENT MARKET SITUATION:
{json.dumps(market_data, indent=2)}

YOUR ACCOUNT:
Balance: ${balance:.2f}
Open positions: {json.dumps(open_positions, indent=2)}

RECENT TRADE HISTORY (last 10 trades):
{json.dumps(trade_history, indent=2)}

TASK:
Analyse the market situation deeply.
Think like an experienced trader who has seen thousands of charts.
Consider all the data together — not just individual indicators.
Make a trading decision.

Respond ONLY with a JSON object. No explanation outside the JSON.
No markdown. No backticks. Pure JSON only.

Format:
{{
  "action": "trade" or "skip" or "wait",
  "pair": "ETH/USDT" or "SOL/USDT",
  "direction": "long" or "short" or null,
  "mode": "scalp" or "swing" or null,
  "confidence": 0-100,
  "capital_pct": 0.05 to 0.15,
  "entry": price or null,
  "sl": stop_loss_price or null,
  "tp": take_profit_price or null,
  "reasoning": "plain English explanation of why",
  "market_mood": "bullish" or "bearish" or "choppy" or "neutral",
  "risk_level": "low" or "medium" or "high"
}}

If action is skip or wait, set direction/mode/entry/sl/tp to null.
If you see both ETH and SOL setups, pick the better one.
Be honest — if market is choppy or unclear, say skip.
"""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {
                "role": "user",
                "content": context
            }
        ],
        "temperature": 0.3,
        "max_tokens": 1000,
    }

    try:
        response = requests.post(
            GROQ_URL,
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code != 200:
            print(f"[Tess Brain] API error — {response.status_code} — {response.text}")
            return None

        content = response.json()["choices"][0]["message"]["content"]
        content = content.strip()

        # Clean up any accidental markdown
        content = content.replace("```json", "").replace("```", "").strip()

        decision = json.loads(content)
        return decision

    except json.JSONDecodeError as e:
        print(f"[Tess Brain] Could not parse decision — {e}")
        return None
    except Exception as e:
        print(f"[Tess Brain] Error — {e}")
        return None


def collect_market_data(exchange, pairs, timeframes):
    """
    Collect all market data Tess needs to think.
    Returns a rich picture of current market conditions.
    """
    import pandas as pd

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

    market_data = {}

    for pair in pairs:
        market_data[pair] = {}

        for tf in timeframes:
            try:
                limit = 250 if tf in ["1h", "4h"] else 100
                ohlcv = exchange.fetch_ohlcv(pair, tf, limit=limit)
                df = pd.DataFrame(
                    ohlcv,
                    columns=["time","open","high","low","close","volume"]
                )
                df["time"] = pd.to_datetime(df["time"], unit="ms")

                close  = df["close"]
                price  = round(close.iloc[-1], 4)
                e9     = ema(close, 9)
                e20    = ema(close, 20)
                rsi_s  = calc_rsi(close)
                atr_s  = calc_atr(df)

                # EMA 200 only if enough data
                e200_val = None
                trend    = "unknown"
                if len(close) >= 200:
                    e200     = ema(close, 200)
                    e200_val = round(e200.iloc[-1], 4)
                    trend    = "uptrend" if price > e200_val else "downtrend"

                # Candle analysis
                last_candle  = df.iloc[-1]
                prev_candle  = df.iloc[-2]
                candle_color = "green" if last_candle["close"] > last_candle["open"] else "red"
                candle_size  = round(abs(last_candle["close"] - last_candle["open"]) / last_candle["open"] * 100, 3)

                # Volume analysis
                avg_vol  = df["volume"].tail(20).mean()
                cur_vol  = df["volume"].iloc[-1]
                vol_ratio = round(cur_vol / avg_vol, 2)

                # Price momentum
                price_5ago = close.iloc[-5]
                momentum   = round((price - price_5ago) / price_5ago * 100, 3)

                # EMA cross
                ema_cross = "none"
                if e9.iloc[-2] <= e20.iloc[-2] and e9.iloc[-1] > e20.iloc[-1]:
                    ema_cross = "bullish_cross"
                elif e9.iloc[-2] >= e20.iloc[-2] and e9.iloc[-1] < e20.iloc[-1]:
                    ema_cross = "bearish_cross"
                elif e9.iloc[-1] > e20.iloc[-1]:
                    ema_cross = "above"
                else:
                    ema_cross = "below"

                # Support and resistance
                recent    = df.tail(20)
                support   = round(recent["low"].min(), 4)
                resistance = round(recent["high"].max(), 4)
                dist_support    = round((price - support) / price * 100, 2)
                dist_resistance = round((resistance - price) / price * 100, 2)

                market_data[pair][tf] = {
                    "price"           : price,
                    "ema9"            : round(e9.iloc[-1], 4),
                    "ema20"           : round(e20.iloc[-1], 4),
                    "ema200"          : e200_val,
                    "trend"           : trend,
                    "rsi"             : round(rsi_s.iloc[-1], 1),
                    "atr"             : round(atr_s.iloc[-1], 4),
                    "ema_cross"       : ema_cross,
                    "volume_ratio"    : vol_ratio,
                    "momentum_5c"     : momentum,
                    "candle_color"    : candle_color,
                    "candle_size_pct" : candle_size,
                    "support"         : support,
                    "resistance"      : resistance,
                    "dist_to_support" : dist_support,
                    "dist_to_resist"  : dist_resistance,
                }

            except Exception as e:
                print(f"[Tess Brain] Data error {pair} {tf} — {e}")
                continue

    return market_data