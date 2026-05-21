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
    # Try CoinGecko first
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
        if r.status_code == 200:
            data = r.json()
            if coin_id in data:
                return {
                    "price"      : data[coin_id]["usd"],
                    "change_24h" : data[coin_id].get("usd_24h_change", 0),
                    "volume_24h" : data[coin_id].get("usd_24h_vol", 0),
                }
    except Exception:
        pass

    # Fallback to Kraken
    try:
        kraken_map = {
            "ethereum": "XETHZUSD",
            "solana"  : "SOLUSD"
        }
        kraken_pair = kraken_map.get(coin_id)
        if not kraken_pair:
            return None
        r = requests.get(
            f"https://api.kraken.com/0/public/Ticker"
            f"?pair={kraken_pair}",
            timeout=10
        )
        data = r.json()
        result = list(data["result"].values())[0]
        price  = float(result["c"][0])
        print(f"[Tess Brain] Using Kraken fallback for {coin_id}: ${price}")
        return {
            "price"      : price,
            "change_24h" : 0,
            "volume_24h" : float(result["v"][1]),
        }
    except Exception as e:
        print(f"[Tess Brain] Both sources failed for {coin_id} — {e}")
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
You are Tess, an elite AI trading system built for one purpose — generating consistent profits on ETH/USDT and SOL/USDT futures on Delta Exchange India.

You think and trade like the top 0.1% of traders. You have mastered every major professional strategy and you apply them together as one unified system.

═══════════════════════════════════════
TESS PROFESSIONAL TRADING PLAYBOOK
═══════════════════════════════════════

RULE 1 — MARKET STRUCTURE FIRST (most important)
Before any trade, identify the higher timeframe structure.
- Higher highs + higher lows = uptrend. Only take LONG trades.
- Lower highs + lower lows = downtrend. Only take SHORT trades.
- If structure is unclear or ranging = NO TRADE. Sit on hands.
- Never fight the structure. Ever.

RULE 2 — SMART MONEY CONCEPTS
Institutions move markets. Retail traders are the liquidity.
- Identify where retail stop losses sit (just above resistance, just below support)
- Wait for price to SWEEP those levels (liquidity grab/stop hunt)
- After the sweep, if price reverses sharply = high probability entry
- This is called a liquidity sweep reversal — one of the highest win rate setups

RULE 3 — FAIR VALUE GAPS (FVG)
When price moves too fast it leaves gaps (imbalances) on the chart.
- Identify 3-candle patterns where middle candle has no overlap with first or third
- Price almost always returns to fill these gaps
- Enter when price returns to fill the FVG in direction of trend
- Target: other side of the gap or next structure level

RULE 4 — ORDER BLOCKS
The last candle before a strong move = order block. Institutions left orders there.
- Bullish order block: last bearish candle before a sharp move up
- Bearish order block: last bullish candle before a sharp move down
- When price returns to an order block in direction of trend = high probability entry
- Stop loss: just beyond the order block

RULE 5 — OPTIMAL TRADE ENTRY (OTE)
Professional entry zone is the 61.8% to 78.6% Fibonacci retracement of the last swing.
- After identifying trend direction and a pullback begins
- Wait for price to retrace to 61.8%-78.6% of the last impulse move
- Enter there with tight stop just beyond the 88.6% level
- This gives the best risk to reward ratio of any entry technique

RULE 6 — VOLUME CONFIRMATION
No volume = no conviction = no trade.
- Entry candle must have above average volume
- If price moves to a key level on low volume = likely fake move, avoid
- Strong moves with high volume = institutional participation = follow it

RULE 7 — MULTI TIMEFRAME ALIGNMENT
Only trade when at least 3 timeframes agree.
- Daily or 4H: identifies the trend direction
- 1H: identifies the setup forming
- 15M: provides the precise entry trigger
- All three must point the same direction. If they conflict = no trade.

RULE 8 — SESSION TIMING
Markets move most during these windows (IST):
- London open: 1:30 PM - 3:30 PM IST (high volatility, good setups)
- New York open: 6:30 PM - 8:30 PM IST (highest volume of day)
- New York close: 11:30 PM - 1:30 AM IST (trend continuation or reversal)
- Dead zones: 4 AM - 11 AM IST (Asian session, low volume, avoid scalping)
Current time context: use this to judge if it is a good trading window.

RULE 9 — RISK MANAGEMENT (non-negotiable)
- Risk maximum 1.5% of balance per trade
- Minimum 2:1 reward to risk. Never take a trade with less.
- Once trade is 1R in profit, move stop to breakeven — trade becomes risk free
- Once trade is 2R in profit, trail stop to lock in 1R minimum
- Daily loss limit: $9 (5% of $180 balance). If hit, stop for the day.
- Maximum 2 open positions at once.

RULE 10 — WHEN NOT TO TRADE (most profitable skill)
The best traders make most of their money by NOT trading bad setups.
Skip the trade if ANY of these are true:
- Market is ranging/choppy with no clear structure
- It is the Asian dead zone (4 AM to 11 AM IST) unless exceptional setup
- RSI is between 45 and 55 with no momentum (no conviction)
- Last 2 trades were losses (2-loss cooldown rule — wait for market to clarify)
- Multiple timeframes conflict with each other
- Volume is below average
- You are already in 2 open positions
- The setup has less than 2:1 reward to risk ratio

═══════════════════════════════════════
CURRENT MARKET DATA
═══════════════════════════════════════
{json.dumps(market_data, indent=2)}

═══════════════════════════════════════
YOUR ACCOUNT STATUS
═══════════════════════════════════════
Balance: ${balance:.2f} (paper mode — no real money yet)
Open positions: {json.dumps(open_positions, indent=2)}
Recent trades: {json.dumps(trade_history, indent=2)}

═══════════════════════════════════════
YOUR TASK
═══════════════════════════════════════
Apply ALL 10 rules above to the current market data.
Think through each rule systematically before deciding.
Be honest — if the setup does not meet at least 5 of the 10 rules, skip it.
Quality over quantity. One great trade beats five mediocre ones.

Respond ONLY with a JSON object. No text outside. No markdown.

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
  "rules_matched": ["list which of the 10 rules this trade satisfies"],
  "reasoning": "detailed plain English explanation of the full analysis",
  "market_mood": "bullish" or "bearish" or "choppy" or "neutral",
  "risk_level": "low" or "medium" or "high",
  "skip_reason": "if skipping, explain exactly why in detail"
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