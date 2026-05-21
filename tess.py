# ============================================
# TESS — Main Engine v8
# AI-powered by Groq LLaMA 3
# Thinks like a trader, acts like a machine
# ============================================

import ccxt
import time
import json
import os
import csv
from datetime import datetime, timedelta
from brain import think, collect_market_data
from alerts import (
    send_email,
    alert_trade_entered,
    alert_trade_closed,
    alert_kill_switch,
    alert_morning_report
)
from config import GROQ_API_KEY

# ══════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════
PAPER_MODE         = True
PAPER_BALANCE      = 180.0
LEVERAGE           = 20
DAILY_LOSS_LIMIT   = 9.0
SOFT_LOSS_LIMIT    = 5.0
SCAN_SECONDS       = 1320  # 22 minutes — fits 65 scans/day within Groq free limit
PAIRS              = ["ETH/USDT", "SOL/USDT"]
TIMEFRAMES         = ["5m", "15m", "1h"]
LOG_FILE           = "trades.csv"
PERF_FILE          = "performance.json"
MAX_OPEN_POSITIONS = 2
SCALP_EXIT_SECONDS = 840
COOLDOWN_MINUTES   = 10
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
trade_history      = []

# ── Logging ───────────────────────────────────
def log_trade(pair, direction, entry, sl, tp,
              close, pnl, status, mode, reason):
    exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow([
                "time","pair","direction","mode",
                "entry","sl","tp","close",
                "pnl","status","reason"
            ])
        w.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            pair, direction, mode,
            round(entry,4), round(sl,4), round(tp,4),
            round(close,4) if close else "",
            round(pnl,2)   if pnl   else "",
            status, reason
        ])

def save_performance():
    if not os.path.exists(LOG_FILE):
        return
    try:
        import pandas as pd
        df     = pd.read_csv(LOG_FILE, on_bad_lines='skip')
        closed = df[df["status"] == "closed"]
        if len(closed) == 0:
            return
        wins   = closed[closed["pnl"] > 0]
        losses = closed[closed["pnl"] <= 0]
        perf   = {
            "total_trades"    : len(closed),
            "wins"            : len(wins),
            "losses"          : len(losses),
            "win_rate_pct"    : round(len(wins)/len(closed)*100,1),
            "total_pnl"       : round(closed["pnl"].sum(),2),
            "current_balance" : round(paper_balance,2),
            "daily_trades"    : daily_trades,
            "last_updated"    : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(PERF_FILE,"w") as f:
            json.dump(perf, f, indent=2)
        print(
            f"[Tess] Win rate: {perf['win_rate_pct']}% | "
            f"P&L: ${perf['total_pnl']} | "
            f"Balance: ${perf['current_balance']}"
        )
    except Exception as e:
        print(f"[Tess] Performance save error — {e}")

def get_recent_trades(n=10):
    if not os.path.exists(LOG_FILE):
        return []
    try:
        import pandas as pd
        df = pd.read_csv(LOG_FILE, on_bad_lines='skip')
        closed = df[df["status"]=="closed"].tail(n)
        return closed.to_dict("records")
    except:
        return []

# ── Position management ───────────────────────
def get_price(pair):
    try:
        coin_map = {"ETH/USDT": "ethereum", "SOL/USDT": "solana"}
        coin_id  = coin_map.get(pair)
        if not coin_id:
            return None
        import requests
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin_id}&vs_currencies=usd",
            timeout=10
        )
        return r.json()[coin_id]["usd"]
    except:
        return None

def enter_trade(decision):
    global paper_balance, open_positions, daily_trades

    pair      = decision["pair"]
    direction = decision["direction"]
    mode      = decision["mode"]
    entry     = decision["entry"]
    sl        = decision["sl"]
    tp        = decision["tp"]
    cap_pct   = min(decision.get("capital_pct", 0.10), 0.15)
    capital   = round(paper_balance * cap_pct, 2)

    if capital < 2:
        print(f"[Tess] Capital too small — skipping")
        return

    size = (capital * LEVERAGE) / entry

    open_positions[pair] = {
        "entry"         : entry,
        "sl"            : sl,
        "tp"            : tp,
        "size"          : size,
        "capital"       : capital,
        "direction"     : direction,
        "mode"          : mode,
        "entry_time"    : datetime.now(),
        "breakeven_set" : False,
        "trailing_set"  : False,
        "reasoning"     : decision.get("reasoning",""),
    }

    daily_trades += 1

    log_trade(
        pair, direction, entry, sl, tp,
        None, None, "open", mode,
        decision.get("reasoning","")
    )

    alert_trade_entered(
        pair, direction, entry, sl, tp,
        decision.get("confidence", 0),
        f"AI Decision:\n{decision.get('reasoning','')}\n\n"
        f"Market mood: {decision.get('market_mood','')}\n"
        f"Risk level: {decision.get('risk_level','')}\n"
        f"Mode: {mode}\n"
        f"Capital deployed: ${capital} at {LEVERAGE}x"
    )

    fee_note = (
        "Exit under 14 mins — ZERO exit fees"
        if mode == "scalp" else
        "Swing — trailing profit"
    )

    print(
        f"\n[Tess AI] ══ {mode.upper()} ENTERED ══\n"
        f"  Pair      : {pair}\n"
        f"  Direction : {direction.upper()}\n"
        f"  Entry     : ${entry}\n"
        f"  SL        : ${sl}\n"
        f"  TP        : ${tp}\n"
        f"  Capital   : ${capital} × {LEVERAGE}x\n"
        f"  Confidence: {decision.get('confidence',0)}%\n"
        f"  Mood      : {decision.get('market_mood','')}\n"
        f"  Risk      : {decision.get('risk_level','')}\n"
        f"  Note      : {fee_note}\n"
        f"  Reasoning : {decision.get('reasoning','')}\n"
    )

def check_positions():
    global paper_balance, daily_loss, daily_profit
    global open_positions, kill_switch_hit
    global consecutive_losses, cooldown_until

    for pair in list(open_positions.keys()):
        pos       = open_positions[pair]
        price     = get_price(pair)
        if not price:
            continue

        direction = pos["direction"]
        entry     = pos["entry"]
        sl        = pos["sl"]
        tp        = pos["tp"]
        size      = pos["size"]
        mode      = pos["mode"]
        elapsed   = (datetime.now()-pos["entry_time"]).seconds

        # Force exit scalp at 14 mins
        force_exit = (mode=="scalp" and elapsed>=SCALP_EXIT_SECONDS)

        # Breakeven
        if not pos["breakeven_set"]:
            unreal = (
                (price-entry)*size if direction=="long"
                else (entry-price)*size
            )
            if unreal >= 0.50:
                open_positions[pair]["sl"]            = entry
                open_positions[pair]["breakeven_set"] = True
                sl = entry
                print(f"[Tess] BREAKEVEN {pair} — ${round(unreal,2)} locked")

        # Trailing
        if pos["breakeven_set"] and not pos["trailing_set"]:
            unreal = (
                (price-entry)*size if direction=="long"
                else (entry-price)*size
            )
            if unreal >= 1.5:
                new_sl = (
                    round(price-(price-entry)*0.35,4)
                    if direction=="long"
                    else round(price+(entry-price)*0.35,4)
                )
                open_positions[pair]["sl"]           = new_sl
                open_positions[pair]["trailing_set"] = True
                sl = new_sl
                print(f"[Tess] TRAILING {pair} SL→${new_sl}")

        hit_sl = (
            (direction=="long"  and price<=sl) or
            (direction=="short" and price>=sl)
        )
        hit_tp = (
            (direction=="long"  and price>=tp) or
            (direction=="short" and price<=tp)
        )

        if hit_sl or hit_tp or force_exit:
            pnl = (
                (price-entry)*size if direction=="long"
                else (entry-price)*size
            )
            pnl            = round(pnl,2)
            paper_balance += pnl

            if force_exit and not hit_sl and not hit_tp:
                reason = f"14 min limit — fee protection | P&L ${pnl}"
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
                pair, direction, entry, price, pnl, reason
            )
            save_performance()
            del open_positions[pair]

            win = "WIN" if pnl>=0 else "LOSS"
            fee = " | Fee SAVED" if mode=="scalp" and elapsed<900 else ""

            print(
                f"\n[Tess AI] ══ CLOSED {win} ══\n"
                f"  Pair    : {pair}\n"
                f"  P&L     : ${pnl}{fee}\n"
                f"  Time    : {elapsed}s\n"
                f"  Reason  : {reason}\n"
                f"  Balance : ${round(paper_balance,2)}\n"
                f"  Today   : +${round(daily_profit,2)} -${round(daily_loss,2)}\n"
            )

            if consecutive_losses >= 2:
                cooldown_until = datetime.now()+timedelta(minutes=COOLDOWN_MINUTES)
                consecutive_losses = 0
                print(f"[Tess] 2 losses — cooling {COOLDOWN_MINUTES} mins")
                send_email(
                    "Tess cooling down",
                    f"2 losses in a row.\nPausing {COOLDOWN_MINUTES} mins.\n"
                    f"Resumes: {cooldown_until.strftime('%H:%M')}"
                )

            if daily_loss >= DAILY_LOSS_LIMIT:
                kill_switch_hit = True
                alert_kill_switch(daily_loss, DAILY_LOSS_LIMIT)

# ── Morning report ────────────────────────────
def maybe_morning_report():
    global last_report_day
    now = datetime.now()
    if (now.hour==7 and now.minute<2 and
            last_report_day!=now.date()):
        last_report_day = now.date()
        if not os.path.exists(LOG_FILE):
            return
        try:
            import pandas as pd
            df    = pd.read_csv(LOG_FILE, on_bad_lines='skip')
            today = df[df["time"].str.startswith(now.strftime("%Y-%m-%d"))]
            closed = today[today["status"]=="closed"]
            wins   = len(closed[closed["pnl"]>0])
            losses = len(closed[closed["pnl"]<=0])
            pnl    = round(closed["pnl"].sum(),2) if len(closed)>0 else 0
            watching = ", ".join([p for p in PAIRS if p not in open_positions])
            alert_morning_report(pnl,len(closed),wins,losses,watching)
        except:
            pass

# ── Main loop ─────────────────────────────────
def run():
    global kill_switch_hit, daily_loss, daily_profit
    global daily_trades, last_day, scan_count
    global cooldown_until, consecutive_losses

    print("=" * 56)
    print("  TESS v8 — AI Brain (Groq LLaMA 3)")
    print(f"  Mode    : {'PAPER' if PAPER_MODE else 'LIVE'}")
    print(f"  Balance : ${paper_balance:,.2f}")
    print(f"  Leverage: {LEVERAGE}x")
    print(f"  Pairs   : {', '.join(PAIRS)}")
    print(f"  Limits  : Soft -${SOFT_LOSS_LIMIT} | Hard -${DAILY_LOSS_LIMIT}")
    print("=" * 56)

    send_email(
        "Tess v8 — AI brain activated",
        f"Tess is now powered by Groq LLaMA 3 AI.\n\n"
        f"She no longer just follows rules.\n"
        f"She thinks about the market and makes\n"
        f"reasoned trading decisions.\n\n"
        f"Every email will now include her full\n"
        f"reasoning in plain English.\n\n"
        f"Balance: ${paper_balance:.2f}\n"
        f"Leverage: {LEVERAGE}x\n"
        f"Daily loss limit: ${DAILY_LOSS_LIMIT}\n\n"
        f"Watching ETH and SOL every {SCAN_SECONDS} seconds."
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
            print("[Tess] New day — counters reset")

        if kill_switch_hit:
            print(f"[Tess] Kill switch — waiting midnight")
            time.sleep(60)
            continue

        if cooldown_until and datetime.now() < cooldown_until:
            mins = int((cooldown_until-datetime.now()).seconds/60)
            print(f"[Tess] Cooling down — {mins} mins left")
            time.sleep(30)
            continue
        elif cooldown_until and datetime.now() >= cooldown_until:
            cooldown_until = None
            print("[Tess] Cooldown over — resuming")

        # Check open positions
        if open_positions:
            check_positions()

        # AI thinking and decision
        if len(open_positions) < MAX_OPEN_POSITIONS:
            scan_count += 1
            print(f"\n[Tess AI] Scan #{scan_count} — thinking...")

            # Collect full market picture
            market_data = collect_market_data(
                exchange, PAIRS, TIMEFRAMES
            )

            # Get recent trade history
            recent_trades = get_recent_trades(10)

            # Get open position summary
            open_summary = {
                pair: {
                    "direction": pos["direction"],
                    "entry"    : pos["entry"],
                    "pnl_est"  : round(
                        (get_price(pair) or pos["entry"] - pos["entry"])
                        * pos["size"], 2
                    )
                }
                for pair, pos in open_positions.items()
            }

            # Ask the AI brain
            decision = think(
                market_data,
                recent_trades,
                paper_balance,
                open_summary
            )

            if decision is None:
                print("[Tess AI] No decision returned — skipping")
            elif decision["action"] == "trade":
                pair = decision.get("pair")
                if pair and pair not in open_positions:
                    # Safety checks before executing
                    if (decision.get("sl") and
                            decision.get("tp") and
                            decision.get("entry")):
                        print(
                            f"[Tess AI] Decision: TRADE {pair} "
                            f"{decision.get('direction','').upper()} — "
                            f"{decision.get('confidence',0)}% confident\n"
                            f"  Reasoning: {decision.get('reasoning','')}"
                        )
                        enter_trade(decision)
                    else:
                        print("[Tess AI] Decision incomplete — skipping")
                else:
                    print(f"[Tess AI] Already in {pair} — skipping")
            elif decision["action"] == "skip":
                print(
                    f"[Tess AI] Decision: SKIP — "
                    f"{decision.get('reasoning','')}"
                )
            elif decision["action"] == "wait":
                print(
                    f"[Tess AI] Decision: WAIT — "
                    f"{decision.get('reasoning','')}"
                )

        maybe_morning_report()

        print(
            f"[Tess AI] Balance: ${round(paper_balance,2)} | "
            f"+${round(daily_profit,2)} -${round(daily_loss,2)} | "
            f"{daily_trades} trades | "
            f"Open: {list(open_positions.keys()) or 'none'} | "
            f"Next: {SCAN_SECONDS}s"
        )

        time.sleep(SCAN_SECONDS)

if __name__ == "__main__":
    run()