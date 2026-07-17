# ==============================================================================
# TESS — Unified Execution Engine & Direct Agentic Decision Loop (v12.13)
# ==============================================================================

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
import re
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request
from alerts import (
    send_email,
    alert_trade_entered,
    alert_trade_closed,
    alert_kill_switch,
    alert_morning_report
)
import fcs_api
import strategies

# Ensure safe workspace directories for smart auto-refresh ignoring
DIARY_DIR = ".tess_diary"
os.makedirs(DIARY_DIR, exist_ok=True)

# ══════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════
PAPER_MODE         = True
PAPER_BALANCE      = 180.0       
DAILY_LOSS_LIMIT   = 12.0        
SOFT_LOSS_LIMIT    = 6.0         
REWARD_TO_RISK     = 3.5         
SCAN_SECONDS       = 300         
PAIRS              = ["ETH/USDT", "SOL/USDT", "BTC/USDT", "XAUT/USDT"]  

PAIR_RULES = {
    "ETH/USDT" : {"min_confidence": 82, "max_capital_pct": 0.14, "max_leverage": 50},
    "SOL/USDT" : {"min_confidence": 82, "max_capital_pct": 0.14, "max_leverage": 50},
    "BTC/USDT" : {"min_confidence": 85, "max_capital_pct": 0.10, "max_leverage": 50},
    "XAUT/USDT": {"min_confidence": 85, "max_capital_pct": 0.10, "max_leverage": 20}, 
}

# Redirect logging and states to prevent reloader restarts
LOG_FILE           = os.path.join(DIARY_DIR, "trades.csv")
THINKING_LOG       = os.path.join(DIARY_DIR, "tess_thinking.log")
PERF_FILE          = os.path.join(DIARY_DIR, "performance.json")
POSITIONS_FILE     = os.path.join(DIARY_DIR, "open_positions.json")  
CONFIG_FILE        = os.path.join(DIARY_DIR, "dynamic_config.json")
BALANCE_FILE       = os.path.join(DIARY_DIR, "balance.json")         

PENDING_FILE       = os.path.join(DIARY_DIR, "pending_approvals.json")
APPROVED_QUEUE     = os.path.join(DIARY_DIR, "approved_queue.json")

MAX_OPEN_POSITIONS = 2
COOLDOWN_MINUTES   = 15
TAKER_FEE_PCT      = 0.0005

from config import GROQ_API_KEY_2, GEMINI_API_KEY
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Runtime state variables
paper_balance         = PAPER_BALANCE
daily_loss            = 0.0
daily_profit          = 0.0
daily_trades          = 0
open_positions        = {}
kill_switch_hit       = False
last_report_day       = None
last_daily_ledger_day = None 
consecutive_losses    = 0
cooldown_until         = None
last_day               = datetime.now().date()
scan_count             = 0
next_planned_trade    = "Analyzing markets to identify next optimal setup..."

COIN_IDS   = {"ETH/USDT": "ethereum", "SOL/USDT": "solana", "BTC/USDT": "bitcoin", "XAUT/USDT": "pax-gold"}
KRAKEN_IDS = {"ETH/USDT": "XETHZUSD", "SOL/USDT": "SOLUSD", "BTC/USDT": "XXBTZUSD", "XAUT/USDT": "PAXGUSD"}


# ══════════════════════════════════════════
# SECTION 1: COGNITIVE MEMORY SYNTHESIZER
# ══════════════════════════════════════════
class MemorySynthesizer:
    LESSON_CACHE_FILE = os.path.join(DIARY_DIR, "trade_lessons.json")
    MAX_LESSONS = 5

    @staticmethod
    def extract_failure_patterns(trades: list) -> list[dict]:
        if not trades:
            return []
        
        df = pd.DataFrame(trades)
        losses = df[df['pnl'] < 0].copy()
        if losses.empty:
            return []
        
        if 'rsi_entry' not in losses.columns:
            losses['rsi_entry'] = 50.0
        if 'adx_entry' not in losses.columns:
            losses['adx_entry'] = 20.0

        losses['rsi_bucket'] = pd.cut(losses['rsi_entry'], bins=[0,30,50,70,100], labels=['OS','NEUT','OB','EXT'])
        losses['adx_bucket'] = pd.cut(losses['adx_entry'], bins=[0,15,25,100], labels=['WEAK','MOD','STRONG'])
        
        cols = ['pair', 'direction', 'mode', 'rsi_bucket', 'adx_bucket']
        patterns = []
        
        for name, group in losses.groupby(cols, observed=False):
            if len(group) >= 2:
                avg_loss = group['pnl'].mean()
                patterns.append({
                    "fingerprint": dict(zip(cols, name)),
                    "count": len(group),
                    "avg_loss": round(avg_loss, 2),
                    "last_occurrence": group['time'].max()
                })
        return sorted(patterns, key=lambda x: x['count'], reverse=True)

    @staticmethod
    def update_lesson_cache(new_trades: list):
        try:
            with open(MemorySynthesizer.LESSON_CACHE_FILE, 'r') as f:
                cache = json.load(f)
        except:
            cache = {"lessons": [], "updated": ""}
        
        new_patterns = MemorySynthesizer.extract_failure_patterns(new_trades)
        existing_map = {json.dumps(l['fingerprint'], sort_keys=True): l for l in cache['lessons']}
        
        for p in new_patterns:
            key = json.dumps(p['fingerprint'], sort_keys=True)
            if key in existing_map:
                existing_map[key]['count'] = p['count']
                existing_map[key]['last_occurrence'] = p['last_occurrence']
            else:
                existing_map[key] = p
        
        cache['lessons'] = sorted(existing_map.values(), key=lambda x: x['count'], reverse=True)[:20]
        cache['updated'] = datetime.utcnow().isoformat()
        
        with open(MemorySynthesizer.LESSON_CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)

    @staticmethod
    def build_prompt_block() -> str:
        try:
            with open(MemorySynthesizer.LESSON_CACHE_FILE, 'r') as f:
                cache = json.load(f)
        except:
            return "COGNITIVE MEMORY: No historical lessons compiled."
        
        active_lessons = [l for l in cache['lessons'] if l['count'] >= 2][:MemorySynthesizer.MAX_LESSONS]
        if not active_lessons:
            return "COGNITIVE MEMORY: No recurring failure patterns found."
        
        block = "═══ COGNITIVE MEMORY: RECURRING FAILURE MODES TO AVOID ═══\n"
        for i, l in enumerate(active_lessons, 1):
            fp = l['fingerprint']
            block += (
                f"{i}. [{l['count']}x Losses | Avg: ${l['avg_loss']:.2f}] "
                f"{fp.get('pair')} {fp.get('direction').upper()} ({fp.get('mode').upper()}) | "
                f"Context: RSI={fp.get('rsi_bucket')}, ADX={fp.get('adx_bucket')}\n"
            )
        block += "══════════════════════════════════════════════════════════\n"
        return block


# ══════════════════════════════════════════
# SECTION 2: THE LIQUIDITY & HEATMAP SAFETY ENGINE
# ══════════════════════════════════════════
class LiquidityEngine:
    def __init__(self):
        self.max_allowed_spread_bps = 15.0  # 15 basis points (0.15%) max spread allowed

    def fetch_live_orderbook_metrics(self, pair):
        """Queries exchange depth to check execution spreads and bids thickness."""
        symbol = pair.replace("/", "")
        url = f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit=20"
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                
                if not bids or not asks:
                    return None
                
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                spread_bps = ((best_ask - best_bid) / best_bid) * 10000
                
                bid_volume = sum(float(b[1]) for b in bids[:5])
                ask_volume = sum(float(a[1]) for a in asks[:5])
                imbalance_ratio = bid_volume / (bid_volume + ask_volume) if (bid_volume + ask_volume) > 0 else 0.5
                
                return {
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread_bps": round(spread_bps, 2),
                    "imbalance_ratio": round(imbalance_ratio, 2)
                }
        except Exception:
            pass
        return None

    def fetch_coinglass_heatmap_data(self, symbol_base):
        """Mathematical fallback modeling liquidation boundaries around swing levels."""
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol_base}USDT"
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                ticker = r.json()
                high = float(ticker["highPrice"])
                low = float(ticker["lowPrice"])
                return [
                    {"price": round(high * 1.015, 2), "type": "short_liquidations"},
                    {"price": round(low * 0.985, 2), "type": "long_liquidations"}
                ]
        except:
            pass
        return []

    def evaluate_entry_safety(self, pair, direction, entry_price):
        """Blocks entries directly into thin books or opposite liquidation cascade zones."""
        symbol_base = pair.split("/")[0]
        
        ob_metrics = self.fetch_live_orderbook_metrics(pair)
        if ob_metrics:
            if ob_metrics["spread_bps"] > self.max_allowed_spread_bps:
                return False, f"BANNED: Bid-ask spread too wide ({ob_metrics['spread_bps']} bps). Avoid execution."
            
            if direction == "long" and ob_metrics["imbalance_ratio"] < 0.25:
                return True, "WARNING: Low buy-side orderbook depth. Restricting leverage to 5x."
            if direction == "short" and ob_metrics["imbalance_ratio"] > 0.75:
                return True, "WARNING: Low sell-side orderbook depth. Restricting leverage to 5x."

        heatmap = self.fetch_coinglass_heatmap_data(symbol_base)
        for cluster in heatmap:
            cluster_price = cluster["price"]
            distance_pct = abs(entry_price - cluster_price) / entry_price
            
            if distance_pct <= 0.01:
                if direction == "long" and cluster_price < entry_price:
                    return False, f"BANNED: Entry directly above major Long Liquidation Pool (${cluster_price}). High cascade risk."
                if direction == "short" and cluster_price > entry_price:
                    return False, f"BANNED: Entry directly below major Short Liquidation Pool (${cluster_price}). High cascade risk."

        return True, "PASSED: Liquidity and liquidation parameters cleared."


# ── Logging & Persistence Helpers ────────────
def write_thinking_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(THINKING_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(f"Log write failed: {e}")

def log_trade(pair, direction, entry, sl, tp, close, pnl, status, mode, reason, rsi_entry=50, adx_entry=20):
    exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["time", "pair", "direction", "mode", "entry", "sl", "tp", "close", "pnl", "status", "reason", "rsi_entry", "adx_entry"])
        w.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            pair, direction, mode,
            round(entry, 4), round(sl, 4), round(tp, 4),
            round(close, 4) if close is not None else "",
            round(pnl, 2)   if pnl   is not None else "",
            status, reason.replace("\n", " "),
            round(rsi_entry, 1), round(adx_entry, 1)
        ])

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

def maybe_morning_report():
    global last_report_day
    now = datetime.now()
    if now.hour == 7 and now.minute < 6 and last_report_day != now.date():
        last_report_day = now.date()
        if not os.path.exists(LOG_FILE):
            return
        try:
            df      = pd.read_csv(LOG_FILE, on_bad_lines="skip")
            df["time"] = df["time"].astype(str)
            yest    = df[df["time"].str.startswith((now - timedelta(days=1)).strftime("%Y-%m-%d"))]
            closed  = yest[yest["status"] == "closed"].copy()
            closed["pnl"] = pd.to_numeric(closed["pnl"], errors="coerce")
            closed = closed.dropna(subset=["pnl"])
            wins    = len(closed[closed["pnl"] > 0])
            losses  = len(closed[closed["pnl"] <= 0])
            pnl     = round(closed["pnl"].sum(), 2) if len(closed) > 0 else 0
            watching = ", ".join(PAIRS)
            alert_morning_report(pnl, len(closed), wins, losses, watching)
        except Exception as e:
            write_thinking_log(f"Error compiling morning report metrics: {e}")

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
        write_thinking_log(f"Primary Binance candle fetch failed: {e}. Routing to KuCoin fallback...")
        try:
            exchange = ccxt.kucoin({"enableRateLimit": True})
            ohlcv = exchange.fetch_ohlcv(pair, timeframe, limit=limit)
            df = pd.DataFrame(
                ohlcv,
                columns=["time", "open", "high", "low", "close", "volume"]
            )
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            write_thinking_log(f"KuCoin fallback successful for {pair} ({timeframe}).")
            return df
        except Exception as ex:
            write_thinking_log(f"All database candle fallbacks failed for {pair}: {ex}")
            
    return None

# ── Indicators (kept here too: enter_trade needs ATR for stop-distance
#    sizing at execution time — this is separate from strategies.py's copy,
#    which is used purely for the AI's decision snapshot) ─────────────────
def calc_atr(df, period=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# ── Sizing Calculations ──────────────────────
def calc_leverage_and_size(confidence, atr, price, balance, pair="ETH/USDT"):
    MAX_RISK_PCT = 0.02  
    max_risk_usd = round(balance * MAX_RISK_PCT, 2)
    max_risk_usd = max(2.0, min(max_risk_usd, 6.0)) 

    sl_dist = atr * 2.5  
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

# ── Direct Execution Logic ───────────────────
def enter_trade(pair, direction, sl, tp, confidence, reasoning, trade_type, ai_entry_price, override_sizing=None):
    global paper_balance, open_positions, daily_trades

    price = get_price(pair)
    if price is None:
        return

    df_15m = get_candles(pair, "15m", limit=50)
    atr_val = 0.5
    if df_15m is not None and len(df_15m) >= 15:
        atr_s = calc_atr(df_15m)
        atr_val = float(atr_s.iloc[-1]) if not pd.isna(atr_s.iloc[-1]) else 0.5

    if override_sizing:
        # Manual override trade — honor the exact size/leverage/margin already
        # computed by chat_listener.py instead of recalculating from scratch.
        leverage = override_sizing.get("leverage", 20)
        size = override_sizing.get("size")
        capital = override_sizing.get("margin_usd", paper_balance * 0.12)
        sl_dist = abs(price - sl) if sl else atr_val * 2.5
        tp_dist = abs(tp - price) if tp else sl_dist * REWARD_TO_RISK
        exp_profit = round(size * tp_dist, 2)
        fees = round(size * price * TAKER_FEE_PCT * 2, 4)
    else:
        capital, leverage, size, exp_profit, max_loss, fees = calc_leverage_and_size(
            confidence, atr_val, price, paper_balance, pair
        )

    if tp is None or sl is None:
        sl_dist = atr_val * 2.5
        tp_dist = sl_dist * REWARD_TO_RISK
        sl = round(price - sl_dist, 4) if direction == "long" else round(price + sl_dist, 4)
        tp = round(price + tp_dist, 4) if direction == "long" else round(price - tp_dist, 4)

    net_profit = exp_profit - fees
    if net_profit < 2.50:
        write_thinking_log(f"Sizing model rejected: expected reward too narrow: ${net_profit:.2f}.")
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

        # Swing trades get a stagnation exit after 6 hours if going nowhere.
        # Position trades are meant to be patient — no forced timeout, they
        # rely purely on SL/TP.
        force_scalp_exit = (
            trade_type == "swing"
            and elapsed >= 21600
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
    global PAIRS, PAIR_RULES, MAX_OPEN_POSITIONS, last_daily_ledger_day, next_planned_trade
    
    load_positions()
    load_balance()  

    recent_trades = load_recent_trades(100)
    MemorySynthesizer.update_lesson_cache(recent_trades)

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
            print(f"\n[{now.strftime('%H:%M:%S')}] Day transition detected. Counters reset.")
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
        print(f"\n[{now.strftime('%H:%M:%S')}] Executing Scan #{scan_count}...")
        write_thinking_log(f"Executing Scan #{scan_count}")

        if len(open_positions) < MAX_OPEN_POSITIONS:
            market_data = {}
            for pair in PAIRS:
                if pair in open_positions:
                    continue

                candles = {}
                for label, tf, limit in strategies.TIMEFRAMES:
                    candles[label] = get_candles(pair, tf, limit=limit)

                snap = strategies.build_market_snapshot(pair, candles)
                if not snap or not snap.get("tf_1h"):
                    print(f" ⚠️  Setup skipped for {pair}: Insufficient API snapshot data.")
                    continue

                market_data[pair] = snap
                print(f" -> {pair}: Snap loaded (15m/1h/4h). Querying Nemotron-550B...")
                time.sleep(1)

            if market_data:
                recent = load_recent_trades(15)
                ai_response, decision_summary, reasoning_trace = strategies.ask_tess(
                    market_snapshots=market_data,
                    open_positions=open_positions,
                    balance=paper_balance,
                    daily_pnl=(daily_profit - daily_loss),
                    recent_trades=recent,
                    lessons_context=MemorySynthesizer.build_prompt_block()
                )
                if decision_summary:
                    next_planned_trade = decision_summary
                    print(f"[Tess] Decision: {decision_summary}")
                if reasoning_trace:
                    write_thinking_log(f"Nemotron-550B Thinking Trace:\n{reasoning_trace}\n[End of Trace]")
                elif ai_response is None:
                    write_thinking_log(reasoning_trace or "Nemotron call failed with no detail.")

                if ai_response:
                    for decision in ai_response.get("decisions", []):
                        pair = decision.get("pair")
                        action = decision.get("action", "skip")

                        if action in ("long", "short") and pair not in open_positions:
                            # ── RUN INTENT-DRIVEN LIQUIDITY & SPREAD SAFETY OVERLAYS ──
                            from liquidity_engine import LiquidityEngine
                            liq_engine = LiquidityEngine()
                            
                            is_safe, reason = liq_engine.evaluate_entry_safety(
                                pair=pair,
                                direction=action,
                                entry_price=decision.get("entry_price")
                            )
                            
                            if not is_safe:
                                print(f" ⚠️  Trade Blocked by Liquidity Engine for {pair}: {reason}")
                                write_thinking_log(f"Liquidity Engine Blocked trade for {pair}: {reason}")
                                continue
                                
                            print(f" ✅  {pair}: Liquidity check passed. Proceeding with execution...")
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

        if now.hour == 23 and now.minute >= 55 and last_daily_ledger_day != now.date():
            last_daily_ledger_day = now.date()
            from chat_listener import send_daily_ledger_email
            report_thread = threading.Thread(target=send_daily_ledger_email)
            report_thread.start()

        time.sleep(SCAN_SECONDS)


# ══════════════════════════════════════════
# SECTION 2: APPROVAL GATE (Manual Override / Email-Approved Trades)
# ══════════════════════════════════════════
class ApprovalGate:
    @staticmethod
    def process_approved_queue():
        global open_positions

        if not os.path.exists(APPROVED_QUEUE):
            return

        try:
            with open(APPROVED_QUEUE, "r") as f:
                queue = json.load(f)
        except Exception:
            return

        if not queue:
            return

        # Clear the queue immediately to prevent reprocessing the same items
        # on the next 5-second cycle while we're working through this batch.
        try:
            with open(APPROVED_QUEUE, "w") as f:
                json.dump([], f)
        except Exception as e:
            write_thinking_log(f"ApprovalGate: failed to clear queue file: {e}")
            return

        for item in queue:
            try:
                # Case 1: manual override shape -> {"trade": {...}, "sizing": {...}}
                if "trade" in item:
                    decision = item["trade"]
                    override_sizing = item.get("sizing")
                # Case 2: email-approved proposal -> decision dict directly
                else:
                    decision = item
                    override_sizing = None

                pair = decision.get("pair")
                action = decision.get("action")

                if not pair or action not in ("long", "short"):
                    write_thinking_log(f"ApprovalGate: skipped malformed queue item: {item}")
                    continue

                if pair in open_positions:
                    write_thinking_log(f"ApprovalGate: skipped {pair} — position already open.")
                    continue

                if len(open_positions) >= MAX_OPEN_POSITIONS and not override_sizing:
                    write_thinking_log(f"ApprovalGate: skipped {pair} — max open positions reached.")
                    continue

                enter_trade(
                    pair=pair,
                    direction=action,
                    sl=decision.get("sl_price"),
                    tp=decision.get("tp_price"),
                    confidence=int(decision.get("confidence", 100)),
                    reasoning=decision.get("reasoning", "Approved via queue."),
                    trade_type=decision.get("trade_type", "swing"),
                    ai_entry_price=decision.get("entry_price"),
                    override_sizing=override_sizing
                )
                write_thinking_log(f"ApprovalGate: executed approved trade for {pair} ({action}).")

            except Exception as e:
                write_thinking_log(f"ApprovalGate: error processing queue item {item}: {e}")


def queue_processing_thread():
    """
    Dedicated parallel background thread that scans approved_queue.json 
    every 5 seconds, executing manual overrides instantly.
    """
    write_thinking_log("Immediate Executive Approval Queue Thread started.")
    while True:
        try:
            ApprovalGate.process_approved_queue()
        except Exception as e:
            write_thinking_log(f"Error in approval queue thread: {e}")
        time.sleep(5)


# ── Flask Server Definition ──────────────────
app = Flask(__name__, template_folder="templates")

from dev_console import register_dev_console
register_dev_console(app)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def get_status():
    global paper_balance, open_positions, next_planned_trade
    trades = []
    total_profit_td = 0.0
    total_loss_td = 0.0
    
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            if len(lines) > 1:
                headers = [h.strip().lower() for h in lines[0].split(",")]
                
                for line in lines[1:]:
                    if not line.strip():
                        continue
                    try:
                        row_parts = [r.strip() for r in line.split(",")]
                        if len(row_parts) < len(headers):
                            continue
                        
                        row = dict(zip(headers, row_parts))
                        
                        pnl_raw = row.get("pnl") or "0"
                        time_raw = row.get("time") or row.get("exit_time") or ""
                        pair_raw = row.get("pair") or ""
                        direction_raw = row.get("direction") or ""
                        entry_raw = row.get("entry") or "0"
                        close_raw = row.get("close") or row.get("exit") or "0"
                        status_raw = row.get("status") or row.get("result") or ""
                        reason_raw = row.get("reason") or row.get("exit_type") or ""
                        
                        try:
                            pnl_val = float(pnl_raw)
                            close_val = float(close_raw)
                            entry_val = float(entry_raw)
                        except:
                            continue
                        
                        is_closed = (
                            status_raw.lower() in ("closed", "win", "loss") or 
                            (close_val > 0 and pnl_val != 0)
                        )
                        
                        if is_closed:
                            if pnl_val > 0:
                                total_profit_td += pnl_val
                            else:
                                total_loss_td += abs(pnl_val)
                            
                            trades.append({
                                "time": time_raw,
                                "pair": pair_raw,
                                "direction": direction_raw,
                                "entry": entry_val,
                                "close": close_val,
                                "pnl": pnl_val,
                                "status": "closed",
                                "reason": reason_raw
                            })
                    except:
                        continue
                        
                trades.reverse()
                trades = trades[:20]
        except Exception as e:
            print(f"Error parsing status trades: {e}")
            
    load_balance()
    return jsonify({
        "balance": paper_balance,
        "positions": open_positions,
        "trades": trades,
        "next_planned": next_planned_trade,
        "total_profit_td": round(total_profit_td, 2),
        "total_loss_td": round(total_loss_td, 2)
    })

# --- OVERRIDDEN DIRECT LOG FILE ARCHIVE ENGINE ---
@app.route("/api/trades")
def get_trades():
    limit = min(int(request.args.get("limit", 50)), 500)
    offset = max(int(request.args.get("offset", 0)), 0)
    status_filter = request.args.get("status", "all").lower()
    pair_filter = request.args.get("pair", "all").lower()
    search = request.args.get("q", "").strip().lower()

    trades = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Multi-null checks prevents NoneType crashes on trailing commas
                    normalized_row = {k.strip().lower(): v for k, v in row.items() if k is not None}
                    
                    p_status = normalized_row.get("status", "closed").lower()
                    p_pair = normalized_row.get("pair", "").lower()
                    p_reason = normalized_row.get("reason", "").lower()
                    p_direction = normalized_row.get("direction", "").lower()
                    
                    if status_filter != "all" and p_status != status_filter:
                        continue
                    if pair_filter != "all" and p_pair != pair_filter:
                        continue
                    if search:
                        if search not in p_pair and search not in p_reason and search not in p_direction:
                            continue
                            
                    try:
                        pnl_val = normalized_row.get("pnl")
                        pnl_float = float(pnl_val) if pnl_val and pnl_val.strip() else ""
                    except:
                        pnl_float = ""

                    trades.append({
                        "time": normalized_row.get("time", ""),
                        "pair": normalized_row.get("pair", "").upper(),
                        "direction": normalized_row.get("direction", "").upper(),
                        "mode": normalized_row.get("mode", ""),
                        "entry": float(normalized_row.get("entry", 0) or 0),
                        "sl": float(normalized_row.get("sl", 0) or 0),
                        "tp": float(normalized_row.get("tp", 0) or 0),
                        "close": float(normalized_row.get("close", 0) or 0) if normalized_row.get("close") else "",
                        "pnl": pnl_float,
                        "status": normalized_row.get("status", "closed"),
                        "reason": normalized_row.get("reason", "")
                    })
        except Exception as e:
            print(f"Error compiling direct archive trades: {e}")

    # Compute operational statistics dynamically from history file
    total_trades = len(trades)
    wins = sum(1 for t in trades if t["pnl"] != "" and float(t["pnl"]) > 0)
    losses = sum(1 for t in trades if t["pnl"] != "" and float(t["pnl"]) <= 0)
    win_rate = round((wins / total_trades) * 100, 1) if total_trades > 0 else 0.0
    net_pnl = round(sum(float(t["pnl"]) for t in trades if t["pnl"] != ""), 2)
    best_trade = round(max((float(t["pnl"]) for t in trades if t["pnl"] != ""), default=0), 2)

    trades.reverse()
    paged_trades = trades[offset : offset + limit]
    pairs = sorted(list({t["pair"] for t in trades if t["pair"]}))

    return jsonify({
        "trades": paged_trades,
        "total": len(trades),
        "offset": offset,
        "limit": limit,
        "stats": {
            "total": total_trades,
            "wins": wins,
            "losses": losses,
            "winrate": win_rate,
            "net": net_pnl,
            "best": best_trade
        },
        "pairs": pairs
    })

@app.route("/api/config")
def get_config():
    cfg = fcs_api.load_dynamic_config()
    balance = fcs_api.load_balance()
    return jsonify({
        "config": cfg,
        "balance": balance,
        "risk_per_trade_pct": 2.0,
        "risk_per_trade_usd": round(min(max(balance * 0.02, 2.0), 6.0), 2),
        "daily_loss_limit": 12.0,
        "soft_loss_limit": 6.0,
        "reward_to_risk": 3.5,
        "max_open_positions": cfg.get("max_open_positions", 2),
    })

@app.route("/api/system/files")
def system_files():
    return jsonify({"files": fcs_api.list_system_files()})

@app.route("/api/system/file/<path:filename>")
def system_file(filename):
    data = fcs_api.read_system_file(filename)
    if not data:
        return jsonify({"error": "File not found or not allowed"}), 404
    return jsonify(data)

@app.route("/api/system/logs")
def system_logs():
    lines = int(request.args.get("lines", 100))
    log_lines = fcs_api.tail_thinking_log(min(lines, 500))
    feed = fcs_api.parse_scan_feed(log_lines)
    return jsonify({"lines": log_lines, "feed": feed})

@app.route("/api/market/news")
def market_news():
    limit = min(int(request.args.get("limit", 15)), 30)
    return jsonify({"news": fcs_api.fetch_market_news(limit)})

@app.route("/api/market/prices")
def market_prices():
    cfg = fcs_api.load_dynamic_config()
    prices = {}
    for pair in cfg.get("active_pairs", list(fcs_api.COIN_IDS.keys())):
        p = fcs_api.get_price(pair)
        if p:
            prices[pair] = round(p, 4)
    return jsonify({"prices": prices, "timestamp": datetime.now().strftime("%H:%M:%S")})

@app.route("/api/voice_command", methods=["POST"])
def voice_command():
    from chat_listener import generate_conversation_reply
    data = request.json
    command = data.get("command", "")
    if not command:
        return jsonify({"reply": "I couldn't hear any command, Harshit."})
    reply = generate_conversation_reply(command)
    return jsonify({"reply": reply})

@app.route("/api/chat_history")
def get_chat_history():
    from chat_listener import CHAT_HISTORY_FILE
    if os.path.exists(CHAT_HISTORY_FILE):
        try:
            with open(CHAT_HISTORY_FILE, "r") as f:
                history = json.load(f)
                return jsonify({"history": history})
        except:
            pass
    return jsonify({"history": []})

def run_flask():
    """Runs the web server in the main thread with Hot-Reload cleanly re-enabled."""
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR) 
    print("[Tess Cockpit] Live dashboard active at: http://localhost:5000")
    app.run(port=5000, host="0.0.0.0", use_reloader=True)



def telegram_loop():
    """
    Background loop that continuously checks for your mobile Telegram commands.
    """
    try:
        # Dynamically import the check function from chat_listener to prevent circular imports
        from chat_listener import check_telegram_commands
        write_thinking_log("Conversational Telegram listener thread started.")
        check_telegram_commands()
    except Exception as e:
        write_thinking_log(f"Telegram listener loop encountered an error: {e}")


if __name__ == "__main__":
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        try:
            from chat_listener import check_and_reply_to_emails
            def email_loop():
                write_thinking_log("Conversational email listener thread started.")
                while True:
                    check_and_reply_to_emails()
                    time.sleep(180)  
            
            listener_thread = threading.Thread(target=email_loop, daemon=True)
            listener_thread.start()
        except Exception as e:
            print(f"[Tess] Conversational listener thread failed to start: {e}")

        # --- Start the Mobile Telegram Bot Thread ---
        tg_thread = threading.Thread(target=telegram_loop, daemon=True)
        tg_thread.start()

        # Start immediate executive approval queue processor thread
        queue_thread = threading.Thread(target=queue_processing_thread, daemon=True)
        queue_thread.start()

        scanner_thread = threading.Thread(target=run, daemon=True)
        scanner_thread.start()

    run_flask()