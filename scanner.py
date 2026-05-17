# ============================================
# TESS — Complete Brain v7
# FINAL VERSION
# Scalp + Swing Intelligence
# Lower daily loss protection
# Trades when market has ANY movement
# Built for $180 on Delta Exchange India
# ETH/USDT and SOL/USDT
# ============================================

import ccxt
import pandas as pd
import numpy as np
import time
import json
import os
import csv
from datetime import datetime, timedelta
from alerts import (
    send_email,
    alert_trade_entered,
    alert_trade_closed,
    alert_signal_skipped,
    alert_kill_switch,
    alert_morning_report
)

# ══════════════════════════════════════════════
# SETTINGS — change only these
# ══════════════════════════════════════════════
PAPER_MODE           = True
PAPER_BALANCE        = 180.0
LEVERAGE             = 20
MIN_CONFIDENCE       = 55       # low enough to catch real setups
SCALP_MIN_PROFIT     = 1      # $0.30 minimum — realistic at $180
SWING_MIN_PROFIT     = 3      # $1.50 minimum for swing
STOP_LOSS_PCT        = 0.10     # 10% of capital per trade
DAILY_LOSS_LIMIT     = 9.0      # hard stop at $9 loss per day (5%)
SOFT_LOSS_LIMIT      = 5.0      # slow down after $5 loss
REWARD_TO_RISK_SCALP = 1.5
REWARD_TO_RISK_SWING = 3.0
SCAN_SECONDS         = 60       # scan every 60 seconds
SCALP_EXIT_SECONDS   = 840      # force exit scalp at 14 mins
PAIRS                = ["ETH/USDT", "SOL/USDT"]
SCALP_TIMEFRAMES     = ["1m", "3m", "5m"]
SWING_TIMEFRAMES     = ["15m", "1h"]
LOG_FILE             = "trades.csv"
PERF_FILE            = "performance.json"
MAX_OPEN_POSITIONS   = 2
COOLDOWN_AFTER_LOSS  = 10       # minutes to wait after 2 losses
DAILY_TRADE_TARGET   = 50
# ══════════════════════════════════════════════

exchange = ccxt.binance({"enableRateLimit": True})

# ── State ─────────────────────────────────────
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

# ── Logging ───────────────────────────────────
def log_trade(pair, direction, entry, sl, tp,
              close, pnl, status, mode, reason):
    exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow([
                "time", "pair", "direction", "mode",
                "entry", "sl", "tp", "close",
                "pnl", "status", "reason"
            ])
        w.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            pair, direction, mode,
            round(entry, 4), round(sl, 4), round(tp, 4),
            round(close, 4) if close else "",
            round(pnl,   2) if pnl   else "",
            status, reason
        ])

def save_performance():
    if not os.path.exists(LOG_FILE):
        return
    try:
        df = pd.read_csv(LOG_FILE, on_bad_lines='skip')
    except Exception as e:
        print(f"[Tess] Log file issue — resetting")
        os.remove(LOG_FILE)
        return
    closed = df[df["status"] == "closed"]
    if len(closed) == 0:
        return
    wins   = closed[closed["pnl"] > 0]
    losses = closed[closed["pnl"] <= 0]
    scalps = closed[closed["mode"] == "scalp"]
    swings = closed[closed["mode"] == "swing"]
    perf   = {
        "total_trades"    : len(closed),
        "wins"            : len(wins),
        "losses"          : len(losses),
        "win_rate_pct"    : round(
            len(wins)/len(closed)*100, 1
        ),
        "total_pnl"       : round(closed["pnl"].sum(), 2),
        "scalp_trades"    : len(scalps),
        "swing_trades"    : len(swings),
        "scalp_pnl"       : round(
            scalps["pnl"].sum(), 2
        ) if len(scalps) > 0 else 0,
        "swing_pnl"       : round(
            swings["pnl"].sum(), 2
        ) if len(swings) > 0 else 0,
        "current_balance" : round(paper_balance, 2),
        "daily_trades"    : daily_trades,
        "last_updated"    : datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }
    with open(PERF_FILE, "w") as f:
        json.dump(perf, f, indent=2)
    print(
        f"[Tess] Stats — "
        f"Win rate: {perf['win_rate_pct']}% | "
        f"P&L: ${perf['total_pnl']} | "
        f"Balance: ${perf['current_balance']} | "
        f"Trades: {daily_trades}"
    )

# ── Market data ───────────────────────────────
def get_candles(pair, timeframe, limit=100):
    try:
        ohlcv = exchange.fetch_ohlcv(
            pair, timeframe, limit=limit
        )
        df = pd.DataFrame(
            ohlcv,
            columns=["time","open","high",
                     "low","close","volume"]
        )
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df
    except Exception as e:
        print(f"[Tess] Data error {pair} {timeframe} — {e}")
        return None

def get_price(pair):
    try:
        return exchange.fetch_ticker(pair)["last"]
    except:
        return None

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

def momentum_score(df):
    closes = df["close"].tail(5).values
    if len(closes) < 5:
        return 0
    return abs((closes[-1]-closes[0])/closes[0]*100)

def is_sideways(df, lookback=10, threshold=0.001):
    recent = df["close"].tail(lookback)
    rng    = (recent.max()-recent.min())/recent.mean()
    return rng < threshold

def ema_crossed_up(e9, e20, i):
    if i < 1:
        return False
    return (e9.iloc[i-1] <= e20.iloc[i-1] and
            e9.iloc[i]   >  e20.iloc[i])

def ema_crossed_down(e9, e20, i):
    if i < 1:
        return False
    return (e9.iloc[i-1] >= e20.iloc[i-1] and
            e9.iloc[i]   <  e20.iloc[i])

# ── Position sizing ───────────────────────────
def get_position_size(balance, price, sl):
    capital = balance * 0.15
    capital = min(capital, balance * 0.40)
    if capital < 2:
        return None, None
    size    = (capital * LEVERAGE) / price
    return size, round(capital, 2)

# ── Scoring engine ────────────────────────────
def score_setup(pair, timeframe):
    limit = 50 if timeframe in ["1m","3m","5m"] else 220
    df    = get_candles(pair, timeframe, limit=limit)
    if df is None or len(df) < 25:
        return None

    close = df["close"]
    price = round(close.iloc[-1], 4)
    i     = len(df) - 1

    e9  = ema(close, min(9,  len(close)-1))
    e20 = ema(close, min(20, len(close)-1))
    rsi_s = calc_rsi(close)
    atr_s = calc_atr(df)

    rsi_now = rsi_s.iloc[i]
    atr_now = atr_s.iloc[i]

    if pd.isna(rsi_now) or pd.isna(atr_now) or atr_now == 0:
        return None

    # Only block truly dead markets
    if is_sideways(df):
        return None

    # EMA 200 trend on longer timeframes only
    trend = "neutral"
    if timeframe in ["15m","1h"] and len(close) >= 200:
        e200  = ema(close, 200)
        trend = "up" if price > e200.iloc[i] else "down"

    # Fresh cross — look back 5 candles
    fresh_long  = any(
        ema_crossed_up(e9, e20, max(0, i-k))
        for k in range(min(5, i))
    )
    fresh_short = any(
        ema_crossed_down(e9, e20, max(0, i-k))
        for k in range(min(5, i))
    )

    rsi_oversold   = rsi_now < 35
    rsi_overbought = rsi_now > 65
    mom            = momentum_score(df)

    # Volume — very lenient
    avg_vol = df["volume"].tail(10).mean()
    cur_vol = df["volume"].iloc[-1]
    vol_ok  = cur_vol > avg_vol * 0.5

    # ── Scoring ───────────────────────────────
    long_score  = 0
    short_score = 0
    reasons     = []

    # Trend — 25 points
    if trend == "up":
        long_score  += 25
        reasons.append("Uptrend — above EMA 200")
    elif trend == "down":
        short_score += 25
        reasons.append("Downtrend — below EMA 200")
    else:
        # Neutral — give points based on EMA 9/20
        if e9.iloc[i] > e20.iloc[i]:
            long_score  += 15
        else:
            short_score += 15
        reasons.append("Neutral trend — using EMA 9/20 direction")

    # EMA 9/20 — 25 points
    if fresh_long:
        long_score  += 25
        reasons.append("EMA 9 crossed above EMA 20")
    elif fresh_short:
        short_score += 25
        reasons.append("EMA 9 crossed below EMA 20")
    else:
        if e9.iloc[i] > e20.iloc[i]:
            long_score  += 18
            reasons.append("EMA 9 above EMA 20 — bullish")
        else:
            short_score += 18
            reasons.append("EMA 9 below EMA 20 — bearish")

    # RSI — 20 points
    if rsi_oversold:
        long_score  += 20
        reasons.append(
            f"RSI oversold {round(rsi_now,1)} — bounce"
        )
    elif rsi_overbought:
        short_score += 20
        reasons.append(
            f"RSI overbought {round(rsi_now,1)} — drop"
        )
    else:
        if long_score >= short_score:
            long_score  += 10
        else:
            short_score += 10
        reasons.append(f"RSI {round(rsi_now,1)}")

    # Volume — 10 points
    if vol_ok:
        long_score  += 10
        short_score += 10
        reasons.append("Volume ok")

    # Momentum — 10 points
    if mom > 0.05:
        long_score  += 10
        short_score += 10
        reasons.append(f"Momentum {round(mom,3)}%")

    direction  = "long" if long_score >= short_score else "short"
    confidence = long_score if direction == "long" else short_score
    confidence = min(confidence, 95)

    # ── SL and TP ─────────────────────────────
    sl_pct = STOP_LOSS_PCT / LEVERAGE  # 0.5% price move

    if direction == "long":
        sl       = round(price * (1 - sl_pct), 4)
        tp_scalp = round(
            price + (price-sl) * REWARD_TO_RISK_SCALP, 4
        )
        tp_swing = round(
            price + (price-sl) * REWARD_TO_RISK_SWING, 4
        )
    else:
        sl       = round(price * (1 + sl_pct), 4)
        tp_scalp = round(
            price - (sl-price) * REWARD_TO_RISK_SCALP, 4
        )
        tp_swing = round(
            price - (sl-price) * REWARD_TO_RISK_SWING, 4
        )

    size, capital = get_position_size(paper_balance, price, sl)
    if size is None:
        return None

    profit_scalp   = round(abs(tp_scalp-price)*size, 2)
    profit_swing   = round(abs(tp_swing-price)*size, 2)
    potential_loss = round(abs(sl-price)*size, 2)

    if profit_scalp < SCALP_MIN_PROFIT:
        return None

    return {
        "pair"           : pair,
        "timeframe"      : timeframe,
        "price"          : price,
        "direction"      : direction,
        "confidence"     : confidence,
        "sl"             : sl,
        "tp_scalp"       : tp_scalp,
        "tp_swing"       : tp_swing,
        "atr"            : round(atr_now, 6),
        "rsi"            : round(rsi_now, 1),
        "size"           : size,
        "capital"        : capital,
        "profit_scalp"   : profit_scalp,
        "profit_swing"   : profit_swing,
        "potential_loss" : potential_loss,
        "momentum"       : mom,
        "reasons"        : "\n".join(reasons),
    }

# ── Enter trade ───────────────────────────────
def enter_trade(setup, mode):
    global paper_balance, open_positions, daily_trades

    pair = setup["pair"]
    tp   = (setup["tp_swing"]
            if mode == "swing"
            else setup["tp_scalp"])
    exp  = (setup["profit_swing"]
            if mode == "swing"
            else setup["profit_scalp"])

    open_positions[pair] = {
        "entry"         : setup["price"],
        "sl"            : setup["sl"],
        "tp"            : tp,
        "size"          : setup["size"],
        "capital"       : setup["capital"],
        "direction"     : setup["direction"],
        "mode"          : mode,
        "entry_time"    : datetime.now(),
        "breakeven_set" : False,
        "trailing_set"  : False,
    }

    daily_trades += 1

    log_trade(
        pair, setup["direction"], setup["price"],
        setup["sl"], tp, None, None,
        "open", mode, setup["reasons"]
    )

    alert_trade_entered(
        pair, setup["direction"], setup["price"],
        setup["sl"], tp,
        setup["confidence"], setup["reasons"]
    )

    fee_note = (
        "Exit under 14 mins = ZERO exit fees"
        if mode == "scalp" else
        "Swing — trailing profit, no time limit"
    )

    print(
        f"\n[Tess] ══ {mode.upper()} #{daily_trades} ══\n"
        f"  Pair      : {pair}\n"
        f"  Direction : {setup['direction'].upper()}\n"
        f"  Entry     : ${setup['price']}\n"
        f"  SL        : ${setup['sl']}\n"
        f"  TP        : ${tp}\n"
        f"  Capital   : ${setup['capital']} × {LEVERAGE}x\n"
        f"  Confidence: {setup['confidence']}%\n"
        f"  Expected  : +${exp}\n"
        f"  Note      : {fee_note}\n"
        f"  Balance   : ${round(paper_balance,2)}\n"
    )

# ── Manage positions ──────────────────────────
def check_open_positions():
    global paper_balance, daily_loss, daily_profit
    global open_positions, kill_switch_hit
    global consecutive_losses, cooldown_until

    for pair in list(open_positions.keys()):
        pos        = open_positions[pair]
        price      = get_price(pair)
        if price is None:
            continue

        direction  = pos["direction"]
        entry      = pos["entry"]
        sl         = pos["sl"]
        tp         = pos["tp"]
        size       = pos["size"]
        mode       = pos["mode"]
        elapsed    = (datetime.now()-pos["entry_time"]).seconds

        # Force exit scalp at 14 mins
        force_exit = (
            mode == "scalp" and
            elapsed >= SCALP_EXIT_SECONDS
        )

        # Breakeven — $0.50 profit
        if not pos["breakeven_set"]:
            unreal = (
                (price-entry)*size if direction=="long"
                else (entry-price)*size
            )
            if unreal >= 0.50:
                open_positions[pair]["sl"]            = entry
                open_positions[pair]["breakeven_set"] = True
                sl = entry
                print(
                    f"[Tess] BREAKEVEN {pair} — "
                    f"${round(unreal,2)} locked"
                )

        # Trailing stop on swing
        if (mode == "swing" and
                pos["breakeven_set"] and
                not pos["trailing_set"]):
            unreal = (
                (price-entry)*size if direction=="long"
                else (entry-price)*size
            )
            if unreal >= 1.5:
                new_sl = (
                    round(price-(price-entry)*0.35, 4)
                    if direction == "long"
                    else round(price+(entry-price)*0.35, 4)
                )
                open_positions[pair]["sl"]           = new_sl
                open_positions[pair]["trailing_set"] = True
                sl = new_sl
                print(
                    f"[Tess] TRAILING {pair} "
                    f"SL → ${new_sl}"
                )

        # Exit conditions
        hit_sl = (
            (direction=="long"  and price <= sl) or
            (direction=="short" and price >= sl)
        )
        hit_tp = (
            (direction=="long"  and price >= tp) or
            (direction=="short" and price <= tp)
        )

        if hit_sl or hit_tp or force_exit:
            pnl = (
                (price-entry)*size if direction=="long"
                else (entry-price)*size
            )
            pnl            = round(pnl, 2)
            paper_balance += pnl

            if force_exit and not hit_sl and not hit_tp:
                reason = (
                    f"14 min limit — fee protection | "
                    f"P&L ${pnl}"
                )
            elif hit_tp:
                reason = "Take profit hit"
            else:
                reason = "Stop loss hit"

            if pnl < 0:
                daily_loss         += abs(pnl)
                consecutive_losses += 1
            else:
                daily_profit       += pnl
                consecutive_losses  = 0

            log_trade(
                pair, direction, entry, sl, tp,
                price, pnl, "closed", mode, reason
            )
            alert_trade_closed(
                pair, direction, entry,
                price, pnl, reason
            )
            save_performance()
            del open_positions[pair]

            win_loss = "WIN" if pnl >= 0 else "LOSS"
            fee_msg  = (
                " | Exit fee SAVED"
                if mode=="scalp" and elapsed < 900
                else ""
            )

            print(
                f"\n[Tess] ══ CLOSED {win_loss} ══\n"
                f"  Pair    : {pair}\n"
                f"  Mode    : {mode}\n"
                f"  P&L     : ${pnl}{fee_msg}\n"
                f"  Time    : {elapsed}s\n"
                f"  Reason  : {reason}\n"
                f"  Balance : ${round(paper_balance,2)}\n"
                f"  Today   : "
                f"+${round(daily_profit,2)} "
                f"-${round(daily_loss,2)}\n"
            )

            # Cooldown after 2 losses
            if consecutive_losses >= 2:
                cooldown_until = (
                    datetime.now() +
                    timedelta(minutes=COOLDOWN_AFTER_LOSS)
                )
                consecutive_losses = 0
                print(
                    f"[Tess] 2 losses — cooling down "
                    f"{COOLDOWN_AFTER_LOSS} mins"
                )
                send_email(
                    "Tess — cooling down after 2 losses",
                    f"2 consecutive losses.\n"
                    f"Pausing {COOLDOWN_AFTER_LOSS} mins.\n"
                    f"Resumes: "
                    f"{cooldown_until.strftime('%H:%M')}\n\n"
                    f"Your capital is protected."
                )

            # Hard kill switch — $9 per day
            if daily_loss >= DAILY_LOSS_LIMIT:
                kill_switch_hit = True
                alert_kill_switch(
                    daily_loss, DAILY_LOSS_LIMIT
                )
                print(
                    f"[Tess] KILL SWITCH — "
                    f"$9 daily loss limit hit. "
                    f"Stopped for today."
                )

# ── Soft loss slowdown ────────────────────────
def should_slow_down():
    return daily_loss >= SOFT_LOSS_LIMIT

# ── Morning report ────────────────────────────
def maybe_morning_report():
    global last_report_day
    now = datetime.now()
    if (now.hour == 7 and now.minute < 2 and
            last_report_day != now.date()):
        last_report_day = now.date()
        if not os.path.exists(LOG_FILE):
            return
        df    = pd.read_csv(LOG_FILE)
        today = df[df["time"].str.startswith(
            now.strftime("%Y-%m-%d")
        )]
        closed = today[today["status"] == "closed"]
        wins   = len(closed[closed["pnl"] > 0])
        losses = len(closed[closed["pnl"] <= 0])
        pnl    = (
            round(closed["pnl"].sum(), 2)
            if len(closed) > 0 else 0
        )
        watching = ", ".join([
            p for p in PAIRS
            if p not in open_positions
        ])
        alert_morning_report(
            pnl, len(closed), wins, losses, watching
        )

# ── Main loop ─────────────────────────────────
def run():
    global kill_switch_hit, daily_loss, daily_profit
    global daily_trades, last_day, scan_count, cooldown_until

    print("=" * 56)
    print("  TESS v7 — Final Version")
    print(f"  Mode       : {'PAPER' if PAPER_MODE else 'LIVE'}")
    print(f"  Balance    : ${paper_balance:,.2f}")
    print(f"  Leverage   : {LEVERAGE}x")
    print(f"  Min conf   : {MIN_CONFIDENCE}%")
    print(f"  Scalp min  : ${SCALP_MIN_PROFIT}")
    print(f"  Swing min  : ${SWING_MIN_PROFIT}")
    print(f"  Soft limit : -${SOFT_LOSS_LIMIT}/day")
    print(f"  Hard limit : -${DAILY_LOSS_LIMIT}/day")
    print(f"  Scan every : {SCAN_SECONDS}s")
    print("=" * 56)

    send_email(
        "Tess v7 is live",
        f"Final version running.\n\n"
        f"Balance: ${paper_balance:.2f}\n"
        f"Leverage: {LEVERAGE}x\n"
        f"Min confidence: {MIN_CONFIDENCE}%\n"
        f"Scalp min profit: ${SCALP_MIN_PROFIT}\n"
        f"Swing min profit: ${SWING_MIN_PROFIT}\n\n"
        f"Loss protection:\n"
        f"- Soft limit: slow down after -${SOFT_LOSS_LIMIT}\n"
        f"- Hard stop: kill switch at -${DAILY_LOSS_LIMIT}\n"
        f"- 2 losses in a row: {COOLDOWN_AFTER_LOSS} min pause\n\n"
        f"Scanning ETH and SOL every {SCAN_SECONDS} seconds."
    )

    while True:
        now = datetime.now()

        # Midnight reset
        if now.date() != last_day:
            daily_loss         = 0.0
            daily_profit       = 0.0
            daily_trades       = 0
            kill_switch_hit    = False
            consecutive_losses = 0
            cooldown_until     = None
            last_day           = now.date()
            print("[Tess] New day — all counters reset")

        # Kill switch
        if kill_switch_hit:
            print(
                f"[Tess] Kill switch active — "
                f"daily loss ${round(daily_loss,2)} "
                f"hit limit ${DAILY_LOSS_LIMIT}"
            )
            time.sleep(60)
            continue

        # Cooldown
        if cooldown_until and datetime.now() < cooldown_until:
            mins = int(
                (cooldown_until-datetime.now()).seconds/60
            )
            print(f"[Tess] Cooling down — {mins} mins left")
            time.sleep(30)
            continue
        elif (cooldown_until and
              datetime.now() >= cooldown_until):
            cooldown_until = None
            print("[Tess] Cooldown over — resuming")

        # Check positions
        if open_positions:
            check_open_positions()

        # After soft loss limit — only take
        # high confidence swings
        if should_slow_down():
            min_conf_now = 75
            print(
                f"[Tess] Soft limit hit "
                f"(${round(daily_loss,2)} lost) — "
                f"raising confidence to {min_conf_now}%"
            )
        else:
            min_conf_now = MIN_CONFIDENCE

        # Scan
        if len(open_positions) < MAX_OPEN_POSITIONS:
            scan_count += 1
            for pair in PAIRS:
                if pair in open_positions:
                    continue

                best_scalp = None
                best_swing = None

                # Scalp scans
                for tf in SCALP_TIMEFRAMES:
                    setup = score_setup(pair, tf)
                    if setup is None:
                        continue
                    print(
                        f"[Tess] {pair} {tf} "
                        f"{setup['direction'].upper()} "
                        f"{setup['confidence']}% "
                        f"scalp +${setup['profit_scalp']}"
                    )
                    if setup["confidence"] >= min_conf_now:
                        if (best_scalp is None or
                                setup["confidence"] >
                                best_scalp["confidence"]):
                            best_scalp = setup

                # Swing scans
                for tf in SWING_TIMEFRAMES:
                    setup = score_setup(pair, tf)
                    if setup is None:
                        continue
                    print(
                        f"[Tess] {pair} {tf} "
                        f"{setup['direction'].upper()} "
                        f"{setup['confidence']}% "
                        f"swing +${setup['profit_swing']}"
                    )
                    if (setup["confidence"] >= min_conf_now
                            and setup["profit_swing"]
                            >= SWING_MIN_PROFIT):
                        if (best_swing is None or
                                setup["confidence"] >
                                best_swing["confidence"]):
                            best_swing = setup

                # Decide mode
                if best_swing and best_scalp:
                    if (best_swing["confidence"] >=
                            best_scalp["confidence"] - 5):
                        enter_trade(best_swing, "swing")
                    else:
                        enter_trade(best_scalp, "scalp")
                elif best_swing:
                    enter_trade(best_swing, "swing")
                elif best_scalp:
                    enter_trade(best_scalp, "scalp")
                else:
                    print(
                        f"[Tess] {pair} — "
                        f"no setup above "
                        f"{min_conf_now}%"
                    )

        maybe_morning_report()

        print(
            f"[Tess] Scan #{scan_count} — "
            f"${round(paper_balance,2)} | "
            f"+${round(daily_profit,2)} "
            f"-${round(daily_loss,2)} | "
            f"{daily_trades} trades | "
            f"Open: "
            f"{list(open_positions.keys()) or 'none'}"
        )

        time.sleep(SCAN_SECONDS)

if __name__ == "__main__":
    run()
