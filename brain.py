# ============================================
# TESS — AI Brain v2
# Market data: CoinGecko (no restrictions)
# Groq LLaMA 3 for decisions
# Optimised for 24/7 on Railway free tier
# ============================================

import requests
import json
import time
import pandas as pd
from config import GROQ_API_KEY, GROQ_API_KEY_2
import random

GROQ_URL    = "https://api.groq.com/openai/v1/chat/completions"
GECKO_URL   = "https://api.coingecko.com/api/v3"

COIN_IDS = {
    "ETH/USDT": "ethereum",
    "SOL/USDT": "solana",
}

def get_gecko_price(coin_id):
    try:
        r = requests.get(
            f"{GECKO_URL}/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            },
            timeout=10
        )
        data = r.json()
        return {
            "price"      : data[coin_id]["usd"],
            "change_24h" : data[coin_id].get("usd_24h_change", 0),
            "volume_24h" : data[coin_id].get("usd_24h_vol", 0),
        }
    except Exception as e:
        print(f"[Tess Brain] CoinGecko price error — {e}")
        return None

def get_gecko_candles(coin_id, days=1):
    try:
        r = requests.get(
            f"{GECKO_URL}/coins/{coin_id}/ohlc",
            params={"vs_currency": "usd", "days": days},
            timeout=15
        )
        data = r.json()
        if not data or not isinstance(data, list):
            return None
        df = pd.DataFrame(
            data,
            columns=["time","open","high","low","close"]
        )
        df["time"]   = pd.to_datetime(df["time"], unit="ms")
        df["volume"] = 1000
        return df
    except Exception as e:
        print(f"[Tess Brain] CoinGecko candles error — {e}")
        return None

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

def collect_market_data(exchange, pairs, timeframes):
    market_data = {}

    for pair in pairs:
        coin_id = COIN_IDS.get(pair)
        if not coin_id:
            continue

        market_data[pair] = {}

        # Live price from CoinGecko
        live = get_gecko_price(coin_id)
        if not live:
            continue

        price      = live["price"]
        change_24h = round(live["change_24h"], 2)
        volume_24h = live["volume_24h"]

        # Candles — 1 day for short term, 14 days for longer
        for tf_label, days in [("short", 1), ("medium", 14)]:
            df = get_gecko_candles(coin_id, days=days)
            if df is None or len(df) < 15:
                continue

            close = df["close"]
            i     = len(df) - 1

            e9    = ema(close, min(9,  len(close)-1))
            e20   = ema(close, min(20, len(close)-1))
            rsi_s = calc_rsi(close)
            atr_s = calc_atr(df)

            rsi_now = round(rsi_s.iloc[i], 1)
            atr_now = round(atr_s.iloc[i], 4)

            # EMA 200 if enough data
            e200_val = None
            trend    = "neutral"
            if len(close) >= 200:
                e200     = ema(close, 200)
                e200_val = round(e200.iloc[i], 4)
                trend    = "uptrend" if price > e200_val else "downtrend"
            elif e9.iloc[i] > e20.iloc[i]:
                trend = "uptrend"
            else:
                trend = "downtrend"

            # EMA cross
            ema_cross = "none"
            if i >= 1:
                if (e9.iloc[i-1] <= e20.iloc[i-1] and
                        e9.iloc[i] > e20.iloc[i]):
                    ema_cross = "bullish_cross"
                elif (e9.iloc[i-1] >= e20.iloc[i-1] and
                        e9.iloc[i] < e20.iloc[i]):
                    ema_cross = "bearish_cross"
                elif e9.iloc[i] > e20.iloc[i]:
                    ema_cross = "above"
                else:
                    ema_cross = "below"

            # Support / resistance
            recent     = df.tail(20)
            support    = round(recent["low"].min(), 4)
            resistance = round(recent["high"].max(), 4)

            # Momentum
            if len(close) >= 5:
                mom = round(
                    (close.iloc[-1]-close.iloc[-5]) /
                    close.iloc[-5] * 100, 3
                )
            else:
                mom = 0

            candle_color = (
                "green" if df["close"].iloc[-1] >
                df["open"].iloc[-1] else "red"
            )

            market_data[pair][tf_label] = {
                "price"           : price,
                "ema9"            : round(e9.iloc[i], 4),
                "ema20"           : round(e20.iloc[i], 4),
                "ema200"          : e200_val,
                "trend"           : trend,
                "rsi"             : rsi_now,
                "atr"             : atr_now,
                "ema_cross"       : ema_cross,
                "momentum_5c"     : mom,
                "candle_color"    : candle_color,
                "support"         : support,
                "resistance"      : resistance,
                "change_24h_pct"  : change_24h,
            }

        market_data[pair]["live"] = {
            "price"      : price,
            "change_24h" : change_24h,
            "volume_24h" : round(volume_24h, 0),
        }

        print(
            f"[Tess Brain] {pair} — "
            f"${price} | 24h: {change_24h}% | "
            f"RSI: {rsi_now}"
        )

        time.sleep(2)  # CoinGecko rate limit

    return market_data


def think(market_data, trade_history, balance, open_positions):
    context = f"""
You are Tess, an expert cryptocurrency futures trader.
You trade ETH/USDT and SOL/USDT on Delta Exchange India.
You use 20x leverage. You are disciplined and protect capital above all.

YOUR RULES — never break these:
- Maximum 15% of balance per trade
- Stop loss always set before entry
- Daily loss limit: $9 total
- Scalp trades exit under 14 minutes (zero fees on Delta Exchange)
- Swing trades trail profit and hold as long as trend continues
- Never trade if market data is empty or unclear

CURRENT MARKET:
{json.dumps(market_data, indent=2)}

YOUR ACCOUNT:
Balance: ${balance:.2f}
Open positions: {json.dumps(open_positions, indent=2)}

RECENT TRADE HISTORY:
{json.dumps(trade_history, indent=2)}

TASK:
Analyse the full market picture. Think like an experienced trader.
Consider trend, momentum, RSI, EMA cross, support/resistance together.
Make one clear trading decision.

Respond ONLY with a JSON object. No text outside JSON. No markdown.

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
  "reasoning": "plain English explanation",
  "market_mood": "bullish" or "bearish" or "choppy" or "neutral",
  "risk_level": "low" or "medium" or "high"
}}
"""

    # Rotate between two keys to avoid rate limits
    active_key = GROQ_API_KEY if random.random() > 0.5 else GROQ_API_KEY_2
    headers = {
        "Authorization" : f"Bearer {active_key}",
        "Content-Type"  : "application/json"
    }

    payload = {
        "model"       : "llama-3.3-70b-versatile",
        "messages"    : [{"role": "user", "content": context}],
        "temperature" : 0.3,
        "max_tokens"  : 800,
    }

    try:
        response = requests.post(
            GROQ_URL,
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code == 429:
            print("[Tess Brain] Key 1 rate limited — switching to key 2")
            # Try second key immediately
            headers["Authorization"] = f"Bearer {GROQ_API_KEY_2}"
            response = requests.post(
                GROQ_URL, headers=headers,
                json=payload, timeout=30
            )
            if response.status_code == 429:
                print("[Tess Brain] Both keys limited — waiting 10 mins")
                time.sleep(600)
                return None

        if response.status_code != 200:
            print(
                f"[Tess Brain] API error — "
                f"{response.status_code}"
            )
            return None

        content = (
            response.json()
            ["choices"][0]["message"]["content"]
            .strip()
            .replace("```json","")
            .replace("```","")
            .strip()
        )

        return json.loads(content)

    except json.JSONDecodeError as e:
        print(f"[Tess Brain] JSON parse error — {e}")
        return None
    except Exception as e:
        print(f"[Tess Brain] Error — {e}")
        return None