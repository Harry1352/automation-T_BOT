# ============================================
# TESS — Production Cloud Replica v11.2 (STABLE PROD)
# ============================================

import ccxt
import pandas as pd
import numpy as np
import time
import json
import os
import csv
import requests
import random
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request
from alerts import (
    send_email,
    alert_trade_entered,
    alert_trade_closed,
    alert_kill_switch,
    alert_morning_report
)

# ══════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════
PAPER_MODE         = True
PAPER_BALANCE      = 180.0       
DAILY_LOSS_LIMIT   = 12.0        
SOFT_LOSS_LIMIT    = 6.0         
REWARD_TO_RISK     = 2.5
SCAN_SECONDS       = 300         
PAIRS              = ["ETH/USDT", "SOL/USDT", "BTC/USDT", "XAUT/USDT"]  

PAIR_RULES = {
    "ETH/USDT" : {"min_confidence": 82, "max_capital_pct": 0.14, "max_leverage": 40},
    "SOL/USDT" : {"min_confidence": 82, "max_capital_pct": 0.14, "max_leverage": 40},
    "BTC/USDT" : {"min_confidence": 85, "max_capital_pct": 0.10, "max_leverage": 25},
    "XAUT/USDT": {"min_confidence": 85, "max_capital_pct": 0.10, "max_leverage": 20}, 
}
LOG_FILE           = "trades.csv"
THINKING_LOG       = "tess_thinking.log"
PERF_FILE          = "performance.json"
POSITIONS_FILE     = "open_positions.json"  
CONFIG_FILE        = "dynamic_config.json"
BALANCE_FILE       = "balance.json"         
MAX_OPEN_POSITIONS = 2
COOLDOWN_MINUTES   = 15
TAKER_FEE_PCT      = 0.0005

from config import GROQ_API_KEY, GROQ_API_KEY_2
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# State
paper_balance      = PAPER_BALANCE
daily_loss         = 0.0
daily_profit       = 0.0
daily_trades       = 0
open_positions     = {}
kill_switch_hit    = False
last_report_day    = None
consecutive_losses = 0
cooldown_until     = None
last_day           = datetime.now().date()
scan_count         = 0
next_planned_trade = "Analyzing markets to identify next optimal setup..."

COIN_IDS   = {"ETH/USDT": "ethereum", "SOL/USDT": "solana", "BTC/USDT": "bitcoin", "XAUT/USDT": "pax-gold"}
KRAKEN_IDS = {"ETH/USDT": "XETHZUSD", "SOL/USDT": "SOLUSD", "BTC/USDT": "XXBTZUSD", "XAUT/USDT": "PAXGUSD"}

# ── Flask Server Definition ──────────────────
app = Flask(__name__, template_folder="templates")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def get_status():
    global paper_balance, open_positions, next_planned_trade
    trades = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                trades = list(reader)[-5:] 
                trades.reverse()
        except:
            pass

    return jsonify({
        "balance": paper_balance,
        "positions": open_positions,
        "trades": trades,
        "next_planned": next_planned_trade
    })

@app.route("/api/voice_command", methods=["POST"])
def voice_command():
    from chat_listener import generate_conversation_reply
    data = request.json
    command = data.get("command", "")
    if not command:
        return jsonify({"reply": "I couldn't hear any command, Harshit."})
    reply = generate_conversation_reply(command)
    return jsonify({"reply": reply})

def run_flask():
    """Binds to dyn port for production environments with no reloading loop."""
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR) 
    port = int(os.environ.get("PORT", 5000))
    print(f"[Tess Production] Live dashboard binding to port: {port}")
    # use_reloader=False stops double-spawning in cloud Docker/Railway containers
    app.run(port=port, host="0.0.0.0", use_reloader=False)

# ── Logging & Persistence Engines ────────────
def write_thinking_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(THINKING_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(f"Log write failed: {e}")

def save_balance():
    global paper_balance
    try:
        with open(BALANCE_FILE, "w") as f:
            json.dump({"paper_balance": round(paper_balance, 2)}, f, indent=2)
    except Exception as e:
        write_thinking_log(f"Error saving balance to disk: {e}")

def load_balance():
    global paper_balance
    if not os.path.exists(BALANCE_FILE):
        paper_balance = PAPER_BALANCE
        save_balance()
        return
    try:
        with open(BALANCE_FILE, "r") as f:
            data = json.load(f)
        paper_balance = data.get("paper_balance", PAPER_BALANCE)
    except Exception as e:
        paper_balance = PAPER_BALANCE

def save_positions():
    try:
        serializable = {}
        for pair, pos in open_positions.items():
            pos_copy = pos.copy()
            if isinstance(pos_copy.get("entry_time"), datetime):
                pos_copy["entry_time"] = pos_copy["entry_time"].isoformat()
            serializable[pair] = pos_copy
        with open(POSITIONS_FILE, "w") as f:
            json.dump(serializable, f, indent=2)
    except Exception as e:
        write_thinking_log(f"Error saving positions to disk: {e}")

def load_positions():
    global open_positions
    if not os.path.exists(POSITIONS_FILE):
        open_positions = {}
        return
    try:
        with open(POSITIONS_FILE, "r") as f:
            data = json.load(f)
        loaded = {}
        for pair, pos in data.items():
            if "entry_time" in pos:
                pos["entry_time"] = datetime.fromisoformat(pos["entry_time"])
            loaded[pair] = pos
        open_positions = loaded
    except Exception as e:
        open_positions = {}

# ── Price & Candle Feeders ───────────────────
def get_price(pair):
    try:
        coin_id = COIN_IDS[pair]
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=8
        )
        if r.status_code == 200:
            data = r.json()
            if coin_id in data:
                return float(data[coin_id]["usd"])
    except:
        pass
    try:
        r = requests.get(
            f"https://api.kraken.com/0/public/Ticker?pair={KRAKEN_IDS[pair]}",
            timeout=8
        )
        result = list(r.json()["result"].values())[0]
        return float(result["c"][0])
    except Exception as e:
        write_thinking_log(f"Price error {pair} — {e}")
        return None

def get_candles(pair, timeframe, limit=220):
    try:
        exchange = ccxt.binance({"enableRateLimit": True})
        ohlcv = exchange.fetch_ohlcv(pair, timeframe, limit=limit)
        df = pd.DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"]
        )
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df
    except Exception as e:
        write_thinking_log(f"Candle error {pair} {timeframe} — {e}")
        return None

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

def build_market_snapshot(pair):
    snapshot = {"pair": pair}
    
    for label, tf, limit in [("tf_5m", "5m", 100), ("tf_15m", "15m", 100), ("tf_1h", "1h", 100)]:
        df = get_candles(pair, tf, limit=limit)
        if df is None or len(df) < 30:
            snapshot[label] = None
            continue

        close = df["close"]
        i = len(df) - 1
        price = float(close.iloc[i])

        e9  = ema(close, 9)
        e20 = ema(close, 20)
        rsi = calc_rsi(close)
        atr = calc_atr(df)
        adx_s = calc_adx(df)
        adx_now = round(float(adx_s.iloc[i]), 1) if not pd.isna(adx_s.iloc[i]) else 0.0

        e200_val = None
        trend_200 = "insufficient data"
        if len(close) >= 100:
            e200 = ema(close, min(200, len(close)-1))
            e200_val = round(float(e200.iloc[-1]), 4)
            trend_200 = "above EMA (uptrend)" if price > e200_val else "below EMA (downtrend)"

        ema_cross = "EMA9 above EMA20" if e9.iloc[i] > e20.iloc[i] else "EMA9 below EMA20"

        snapshot[label] = {
            "timeframe"     : tf,
            "price"         : round(price, 4),
            "ema9"          : round(float(e9.iloc[i]), 4),
            "ema20"         : round(float(e20.iloc[i]), 4),
            "trend_200"     : trend_200,
            "ema_cross"     : ema_cross,
            "rsi"           : round(float(rsi.iloc[i]), 1),
            "atr"           : round(float(atr.iloc[i]), 4),
            "adx_trend_strength": adx_now
        }
    return snapshot

# ── Fractional Risk Sizing Calculator ────────
def calc_leverage_and_size(confidence, atr, price, balance, pair="ETH/USDT"):
    MAX_RISK_PCT = 0.02  
    max_risk_usd = round(balance * MAX_RISK_PCT, 2)
    max_risk_usd = max(2.0, min(max_risk_usd, 6.0)) 

    sl_dist = atr * 1.2
    if sl_dist <= 0:
        sl_dist = price * 0.015 
        
    size = max_risk_usd / sl_dist
    position_value = size * price
    
    available_margin = balance * 0.12
    required_leverage = position_value / available_margin
    
    rule = PAIR_RULES.get(pair, {"max_leverage": 40})
    max_lev = rule["max_leverage"]
    leverage = int(max(5, min(required_leverage, max_lev)))
    
    size = (available_margin * leverage) / price
    
    true_max_loss = round(size * sl_dist, 2)
    tp_dist = sl_dist * REWARD_TO_RISK
    exp_profit = round(size * tp_dist, 2)
    fees = round(size * price * TAKER_FEE_PCT * 2, 4)

    return available_margin, leverage, size, exp_profit, true_max_loss, fees

# ── Agentic AI Decision Loop ─────────────────
def ask_tess(market_snapshots, open_positions, balance, daily_pnl, recent_trades, soft_limit_active):
    global next_planned_trade
    trade_summary = []
    for t in recent_trades[-15:]:  
        trade_summary.append({
            "pair"     : t.get("pair"),
            "direction": t.get("direction"),
            "pnl"      : t.get("pnl"),
            "reason"   : t.get("reason", "")[:60],
        })

    prompt = f"""You are Tess, an autonomous AI trading companion.
Analyze these indicators across 3 timeframes (5m, 15m, 1h) and output trading actions.

══════════════════════════════════════
ACCOUNT STATUS
══════════════════════════════════════
Balance: ${balance:.2f} USD
Today P&L: ${daily_pnl:+.2f}
Open positions: {json.dumps(list(open_positions.keys())) if open_positions else "none"}

══════════════════════════════════════
HISTORICAL MEMORY (Learn from past results)
══════════════════════════════════════
{json.dumps(trade_summary, indent=2)}

══════════════════════════════════════
LIVE MARKET snap (5m, 15m, 1H)
══════════════════════════════════════
{json.dumps(market_snapshots, indent=2)}

Think systematically:
1. Ensure the trend on 5m, 15m, and 1h is aligned. No counter-trend trades.
2. If scalp mode is chosen, the target must easily cover fees.
3. If past history shows repeated losses on a specific asset today, stand aside.

Output format MUST be raw JSON:
{{
  "decisions": [
    {{
      "pair": "ETH/USDT" or "SOL/USDT" or "BTC/USDT" or "XAUT/USDT",
      "action": "long" or "short" or "skip",
      "trade_type": "scalp" or "swing",
      "confidence": 0-100,
      "entry_price": number,
      "sl_price": number,
      "tp_price": number,
      "reasoning": "your detailed reasoning"
    }}
  ]
}}"""

    headers = {"Authorization": f"Bearer {GROQ_API_KEY_2}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.25,
        "max_tokens": 1200,
    }

    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=35)
        if r.status_code == 200:
            raw = r.json()["choices"][0]["message"]["content"].strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(raw)
            
            decision = parsed.get("decisions", [{}])[0]
            if decision.get("action") == "skip":
                next_planned_trade = f"Market Scan: Standby. Skipping entries due to: {decision.get('reasoning')[:100]}..."
            else:
                next_planned_trade = f"Planned Setup: {decision.get('trade_type','').upper()} {decision.get('pair')} {decision.get('action','').upper()} | Confidence: {decision.get('confidence')}%"

            return parsed
    except Exception as e:
        write_thinking_log(f"Cloud AI connection issue: {e}")
    return None

# ── Logging Implementation ───────────────────
def log_trade(pair, direction, entry, sl, tp, close, pnl, status, mode, reason):
    exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["time", "pair", "direction", "mode", "entry", "sl", "tp", "close", "pnl", "status", "reason"])
        w.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            pair, direction, mode,
            round(entry, 4), round(sl, 4), round(tp, 4),
            round(close, 4) if close is not None else "",
            round(pnl, 2)   if pnl   is not None else "",
            status, reason.replace("\n", " ")
        ])

def load_recent_trades(n=20):
    if not os.path.exists(LOG_FILE):
        return []
    try:
        df = pd.read_csv(LOG_FILE, on_bad_lines="skip")
        closed = df[df["status"] == "closed"].tail(n)
        return closed.to_dict("records")
    except:
        return []

def save_performance():
    if not os.path.exists(LOG_FILE):
        return
    try:
        df     = pd.read_csv(LOG_FILE, on_bad_lines="skip")
        closed = df[df["status"] == "closed"].copy()
        closed["pnl"] = pd.to_numeric(closed["pnl"], errors="coerce")
        closed = closed.dropna(subset=["pnl"])
        if len(closed) == 0:
            return
        wins   = closed[closed["pnl"] > 0]
        losses = closed[closed["pnl"] <= 0]
        perf = {
            "total_trades"    : len(closed),
            "wins"            : len(wins),
            "losses"          : len(losses),
            "win_rate_pct"    : round(len(wins) / len(closed) * 100, 1),
            "total_pnl"       : round(closed["pnl"].sum(), 2),
            "current_balance" : round(paper_balance, 2),
            "balance_inr"     : round(paper_balance * 85, 0),
            "daily_trades"    : daily_trades,
            "last_updated"    : datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        with open(PERF_FILE, "w") as f:
            json.dump(perf, f, indent=2)
    except Exception as e:
        print(f"[Tess] Performance save error — {e}")

# ── Position Execution Management ────────────
def enter_trade(pair, direction, sl, tp, confidence, reasoning, trade_type, ai_entry_price):
    global paper_balance, open_positions, daily_trades

    price = get_price(pair)
    if price is None:
        return

    df_15m = get_candles(pair, "15m", limit=50)
    atr_val = 0.5
    if df_15m is not None and len(df_15m) >= 15:
        atr_s = calc_atr(df_15m)
        atr_val = float(atr_s.iloc[-1]) if not pd.isna(atr_s.iloc[-1]) else 0.5

    capital, leverage, size, exp_profit, max_loss, fees = calc_leverage_and_size(
        confidence, atr_val, price, paper_balance, pair
    )

    if tp is None or sl is None:
        sl_dist = atr_val * 1.2
        tp_dist = sl_dist * REWARD_TO_RISK
        sl = round(price - sl_dist, 4) if direction == "long" else round(price + sl_dist, 4)
        tp = round(price + tp_dist, 4) if direction == "long" else round(price - tp_dist, 4)

    net_profit = exp_profit - fees
    if net_profit < 2.50:
        write_thinking_log(f"Expected reward is too narrow to justify taker fees: ${net_profit:.2f}. Skip.")
        return

    open_positions[pair] = {
        "entry"         : price,
        "sl"            : sl,
        "tp"            : tp,
        "size"          : size,
        "capital"       : capital,
        "leverage"      : leverage,
        "direction"     : direction,
        "trade_type"    : trade_type or "swing",
        "entry_time"    : datetime.now(),
        "breakeven_set" : False,
        "trailing_set"  : False,
        "confidence"    : confidence,
    }
    daily_trades += 1

    save_positions()  
    log_trade(pair, direction, price, sl, tp, None, None, "open", trade_type or "swing", reasoning)
    alert_trade_entered(pair, direction, price, sl, tp, confidence, reasoning)

    msg = (
        f"Position Established: {direction.upper()} {pair} at {leverage}x.\n"
        f"Target: ${tp} | Risk Limit: ${sl} | Predicted PnL: ${exp_profit:.2f} (Net: ${net_profit:.2f})"
    )
    print(msg)
    write_thinking_log(msg)

def check_open_positions():
    global paper_balance, daily_loss, daily_profit, open_positions, kill_switch_hit, consecutive_losses, cooldown_until

    for pair in list(open_positions.keys()):
        pos = open_positions[pair]
        price = get_price(pair)
        if price is None:
            continue

        direction = pos["direction"]
        entry = pos["entry"]
        sl = pos["sl"]
        tp = pos["tp"]
        size = pos["size"]
        capital = pos["capital"]
        leverage = pos["leverage"]
        trade_type = pos.get("trade_type", "swing")
        elapsed = int((datetime.now() - pos["entry_time"]).total_seconds())

        unreal = (price - entry) * size if direction == "long" else (entry - price) * size

        if not pos["breakeven_set"] and unreal >= 2.0:
            open_positions[pair]["sl"] = entry
            open_positions[pair]["breakeven_set"] = True
            sl = entry
            save_positions()
            write_thinking_log(f"Locked trade at breakeven for {pair}")

        if pos["breakeven_set"] and not pos["trailing_set"] and unreal >= 5.0:
            trail_sl = (
                round(price - (price - entry) * 0.35, 4) if direction == "long"
                else round(price + (entry - price) * 0.35, 4)
            )
            open_positions[pair]["sl"] = trail_sl
            open_positions[pair]["trailing_set"] = True
            sl = trail_sl
            save_positions()

        if pos["trailing_set"]:
            if direction == "long":
                new_sl = round(price - (price - entry) * 0.35, 4)
                if new_sl > open_positions[pair]["sl"]:
                    open_positions[pair]["sl"] = new_sl
                    sl = new_sl
                    save_positions()
            else:
                new_sl = round(price + (entry - price) * 0.35, 4)
                if new_sl < open_positions[pair]["sl"]:
                    open_positions[pair]["sl"] = new_sl
                    sl = new_sl
                    save_positions()

        fees_approx = capital * leverage * TAKER_FEE_PCT * 2
        net_now = unreal - fees_approx

        force_scalp_exit = (
            trade_type == "scalp"
            and elapsed >= 840
            and net_now < 3.00 
        )

        hit_sl = (direction == "long" and price <= sl) or (direction == "short" and price >= sl)
        hit_tp = (direction == "long" and price >= tp) or (direction == "short" and price <= tp)

        if hit_sl or hit_tp or force_scalp_exit:
            pnl = unreal
            paper_balance += pnl
            save_balance()  

            reason = "TP target hit" if hit_tp else ("SL threshold hit" if hit_sl else "Stagnant scalp exit")
            if pnl < 0:
                daily_loss += abs(pnl)
                consecutive_losses += 1
            else:
                daily_profit += pnl
                consecutive_losses = 0

            log_trade(pair, direction, entry, sl, tp, price, pnl, "closed", trade_type, reason)
            alert_trade_closed(pair, direction, entry, price, pnl, reason)
            del open_positions[pair]
            
            save_positions()  
            save_performance()

            msg = f"Closed {pair}: {pnl:+.2f} USD. Reason: {reason}. New Balance: ${paper_balance:.2f}"
            print(msg)
            write_thinking_log(msg)

            if consecutive_losses >= 2:
                cooldown_until = datetime.now() + timedelta(minutes=COOLDOWN_MINUTES)
                consecutive_losses = 0

            if daily_loss >= DAILY_LOSS_LIMIT:
                kill_switch_hit = True
                alert_kill_switch(daily_loss, DAILY_LOSS_LIMIT)

def run():
    global kill_switch_hit, daily_loss, daily_profit, daily_trades, last_day, scan_count, cooldown_until, consecutive_losses
    global PAIRS, PAIR_RULES, MAX_OPEN_POSITIONS  
    
    load_positions()
    load_balance()  

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                d_config = json.load(f)
            PAIRS = d_config.get("active_pairs", PAIRS)
            PAIR_RULES = d_config.get("pair_rules", PAIR_RULES)
            MAX_OPEN_POSITIONS = d_config.get("max_open_positions", MAX_OPEN_POSITIONS)
            write_thinking_log("Dynamic configuration loaded successfully.")
        except Exception as e:
            write_thinking_log(f"Error loading dynamic configuration: {e}")

    msg_start = f"Tess System Online. Monitoring assets: {', '.join(PAIRS)}"
    print(msg_start)
    write_thinking_log(msg_start)

    while True:
        now = datetime.now()

        if now.date() != last_day:
            daily_loss = 0.0
            daily_profit = 0.0
            daily_trades = 0
            kill_switch_hit = False
            consecutive_losses = 0
            cooldown_until = None
            last_day = now.date()
            write_thinking_log("Day transition detected. Counters reset.")

        if kill_switch_hit:
            time.sleep(60)
            continue

        if cooldown_until and datetime.now() < cooldown_until:
            time.sleep(30)
            continue

        if open_positions:
            check_open_positions()

        scan_count += 1
        write_thinking_log(f"Executing Scan #{scan_count}")

        if len(open_positions) < MAX_OPEN_POSITIONS:
            market_data = {}
            for pair in PAIRS:
                if pair in open_positions:
                    continue
                snap = build_market_snapshot(pair)
                market_data[pair] = snap
                time.sleep(1)

            if market_data:
                recent = load_recent_trades(15)
                ai_response = ask_tess(
                    market_snapshots=market_data,
                    open_positions=open_positions,
                    balance=paper_balance,
                    daily_pnl=(daily_profit - daily_loss),
                    recent_trades=recent,
                    soft_limit_active=(daily_loss >= SOFT_LOSS_LIMIT)
                )

                if ai_response:
                    for decision in ai_response.get("decisions", []):
                        pair = decision.get("pair")
                        action = decision.get("action", "skip")

                        write_thinking_log(f"AI Choice for {pair}: {action.upper()} | Reasoning: {decision.get('reasoning')}")

                        if action in ("long", "short") and pair not in open_positions:
                            enter_trade(
                                pair=pair,
                                direction=action,
                                sl=decision.get("sl_price"),
                                tp=decision.get("tp_price"),
                                confidence=int(decision.get("confidence", 0)),
                                reasoning=decision.get("reasoning", ""),
                                trade_type=decision.get("trade_type", "swing"),
                                ai_entry_price=decision.get("entry_price")
                            )

        maybe_morning_report()
        time.sleep(SCAN_SECONDS)

if __name__ == "__main__":
    try:
        from chat_listener import check_and_reply_to_emails
        def email_loop():
            while True:
                check_and_reply_to_emails()
                time.sleep(180)  
        
        listener_thread = threading.Thread(target=email_loop, daemon=True)
        listener_thread.start()
    except Exception as e:
        print(f"[Tess] Conversational listener thread failed to start: {e}")

    # For production environments, run main loop directly on thread
    run()