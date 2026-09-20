# ==============================================================================
# TESS — strategies/backtest_harness.py (v1.0)
# ==============================================================================
# Unified sweep + walk-forward validator for ALL FOUR strategy families.
# Extends the exact methodology already proven in strategy_sweep.py /
# walk_forward_validation.py (the tool that produced Tess's current live
# BTC/SOL parameters) rather than duplicating that logic four times.
#
# Usage (run this file directly from your T_BOT project root):
#   python3 strategies/backtest_harness.py --pair ETH/USDT --family price_action
#   python3 strategies/backtest_harness.py --pair BTC/USDT --family all
#
# What it does, per family:
#   1. SWEEP  — walks every combination in that family's SWEEP_GRID,
#      backtests each on the FULL available window, ranks by net P&L.
#   2. WALK-FORWARD VALIDATE — takes the top N combos from the sweep,
#      splits history into an early half and a late half, and keeps ONLY
#      combos that are profitable on BOTH halves independently. This is
#      the exact bar your current bitcoin.py/solana.py/ethereum.py/xaut.py
#      parameters already had to clear.
#   3. Reports honestly: if nothing survives both halves for a given
#      pair/family, it says so -- exactly like ETH/XAUT already showed
#      zero confirmed edge under the current live system. No result is
#      forced to look better than it is.
#
# Trade simulation logic (entry at signal bar's close, walk forward bar by
# bar checking each subsequent bar's high/low against SL/TP, assume the
# worse outcome if both are touched in the same candle since intrabar
# order can't be known from OHLC alone) is copied from the existing
# backtest.py for consistency with results you already trust.
# ==============================================================================

import argparse
import importlib
import itertools
import sys
import os

import requests
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BINANCE_URL = "https://api.binance.com/api/v3/klines"

FAMILY_MODULES = {
    "technical_analysis": ("technical_analysis.strategy", "implement_technical_analysis_strategy"),
    "price_action": ("price_action.strategy", "implement_price_action_strategy"),
    "fibonacci": ("fibonacci.strategy", "implement_fibonacci_strategy"),
    "price_action_smc": ("price_action_smc.strategy", "implement_price_action_smc_strategy"),
}


# ══════════════════════════════════════════════════════════════════════════
# Candle fetching (same pagination pattern as backtest.py)
# ══════════════════════════════════════════════════════════════════════════

def fetch_candles(symbol="ETHUSDT", interval="1h", total_bars=6000):
    all_rows = []
    end_time = None
    remaining = total_bars
    while remaining > 0:
        batch = min(remaining, 1000)
        params = {"symbol": symbol, "interval": interval, "limit": batch}
        if end_time:
            params["endTime"] = end_time
        resp = requests.get(BINANCE_URL, params=params, timeout=20)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        all_rows = rows + all_rows
        end_time = rows[0][0] - 1
        remaining -= len(rows)

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("open_time")
    return df


# ══════════════════════════════════════════════════════════════════════════
# Trade simulation (matches backtest.py's logic exactly)
# ══════════════════════════════════════════════════════════════════════════

def simulate_trades(signals_df: pd.DataFrame) -> list:
    trades = []
    n = len(signals_df)
    for i in range(n - 1):
        row = signals_df.iloc[i]
        if row["Signal"] == 0:
            continue
        sl, tp = row["Stop_Loss"], row["Take_Profit"]
        if pd.isna(sl) or pd.isna(tp):
            continue
        direction = row["Signal"]
        entry = row["close"]

        outcome, exit_price = None, None
        for j in range(i + 1, n):
            bar = signals_df.iloc[j]
            if direction == 1:
                hit_sl, hit_tp = bar["low"] <= sl, bar["high"] >= tp
            else:
                hit_sl, hit_tp = bar["high"] >= sl, bar["low"] <= tp

            if hit_sl:
                outcome, exit_price = "loss", sl
                break
            elif hit_tp:
                outcome, exit_price = "win", tp
                break

        if outcome:
            risk = abs(entry - sl)
            pnl_r = (exit_price - entry) / risk if direction == 1 else (entry - exit_price) / risk
            trades.append({
                "entry_index": i, "direction": "long" if direction == 1 else "short",
                "outcome": outcome, "signal_type": row.get("Signal_Type", ""),
                "pnl_r": pnl_r,
            })
    return trades


def summarize_trades(trades: list) -> dict:
    if not trades:
        return {"total_trades": 0, "win_rate": 0.0, "net_pnl_r": 0.0, "wins": 0, "losses": 0}
    wins = [t for t in trades if t["outcome"] == "win"]
    net_pnl_r = sum(t["pnl_r"] for t in trades)
    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(trades) - len(wins),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "net_pnl_r": round(net_pnl_r, 2),
    }


# ══════════════════════════════════════════════════════════════════════════
# Run a single (family, param combo) on a given df slice
# ══════════════════════════════════════════════════════════════════════════

def run_single_combo(df: pd.DataFrame, family: str, params: dict) -> dict:
    module_path, fn_name = FAMILY_MODULES[family]
    module = importlib.import_module(module_path)
    strategy_fn = getattr(module, fn_name)

    try:
        out = strategy_fn(df.copy(), **params)
    except Exception as e:
        return {"error": str(e), "total_trades": 0, "win_rate": 0.0, "net_pnl_r": 0.0}

    trades = simulate_trades(out)
    summary = summarize_trades(trades)
    summary["params"] = params
    return summary


def grid_combinations(sweep_grid: dict, max_combos: int = 200):
    """Expands the family's SWEEP_GRID into individual kwarg dicts, capped
    to avoid combinatorial explosion on the biggest grids (price_action_smc
    has 5 swept dims). If the full cartesian product exceeds max_combos,
    samples evenly rather than silently truncating from one end."""
    keys = list(sweep_grid.keys())
    values = list(sweep_grid.values())
    all_combos = list(itertools.product(*values))
    if len(all_combos) > max_combos:
        step = len(all_combos) // max_combos
        all_combos = all_combos[::max(step, 1)][:max_combos]
    return [dict(zip(keys, combo)) for combo in all_combos]


# ══════════════════════════════════════════════════════════════════════════
# Sweep + Walk-forward validation for one family
# ══════════════════════════════════════════════════════════════════════════

def sweep_family(df: pd.DataFrame, family: str, top_n: int = 10, max_combos: int = 200) -> list:
    module_path, _ = FAMILY_MODULES[family]
    module = importlib.import_module(module_path)
    sweep_grid = module.SWEEP_GRID
    combos = grid_combinations(sweep_grid, max_combos=max_combos)

    print(f"  [{family}] sweeping {len(combos)} parameter combinations on full window...")
    results = []
    for params in combos:
        summary = run_single_combo(df, family, params)
        if summary.get("total_trades", 0) >= 5:  # need a minimum sample to mean anything
            results.append(summary)

    results.sort(key=lambda r: r["net_pnl_r"], reverse=True)
    return results[:top_n]


def walk_forward_validate(df: pd.DataFrame, family: str, candidate_params: list) -> list:
    """
    Splits df into early/late halves. A candidate param combo only
    survives if net_pnl_r > 0 on BOTH halves independently -- the exact
    bar bitcoin.py/solana.py/ethereum.py/xaut.py already had to clear.
    Returns the list of survivors with both-half results attached.
    """
    midpoint = len(df) // 2
    early_df = df.iloc[:midpoint]
    late_df = df.iloc[midpoint:]

    survivors = []
    for candidate in candidate_params:
        params = candidate["params"]
        early_result = run_single_combo(early_df, family, params)
        late_result = run_single_combo(late_df, family, params)

        early_ok = early_result.get("total_trades", 0) >= 3 and early_result.get("net_pnl_r", 0) > 0
        late_ok = late_result.get("total_trades", 0) >= 3 and late_result.get("net_pnl_r", 0) > 0

        if early_ok and late_ok:
            survivors.append({
                "params": params,
                "full_window": candidate,
                "early_half": early_result,
                "late_half": late_result,
            })
    return survivors


# ══════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════

def run_pair(pair: str, families: list, interval: str = "1h", total_bars: int = 6000,
             top_n: int = 10, max_combos: int = 200):
    symbol = pair.replace("/", "")
    print(f"\n{'='*70}\n{pair} — {interval} candles, last {total_bars} bars\n{'='*70}")
    df = fetch_candles(symbol, interval, total_bars)
    print(f"Fetched {len(df)} candles: {df.index[0]} -> {df.index[-1]}")

    all_results = {}
    for family in families:
        print(f"\n--- {family} ---")
        top_candidates = sweep_family(df, family, top_n=top_n, max_combos=max_combos)
        if not top_candidates:
            print(f"  No combo produced >=5 trades on the full window. No edge testable here.")
            all_results[family] = {"survivors": [], "note": "insufficient signal volume"}
            continue

        print(f"  Top full-window combo: {top_candidates[0]['params']} "
              f"-> {top_candidates[0]['total_trades']} trades, "
              f"{top_candidates[0]['win_rate']}% WR, {top_candidates[0]['net_pnl_r']}R net")

        print(f"  Walk-forward validating top {len(top_candidates)} candidates (early/late split)...")
        survivors = walk_forward_validate(df, family, top_candidates)

        if not survivors:
            print(f"  HONEST RESULT: no combo survived both halves independently. "
                  f"No confirmed edge for {family} on {pair} at this timeframe.")
            all_results[family] = {"survivors": [], "note": "no combo profitable on both halves"}
        else:
            survivors.sort(key=lambda s: s["early_half"]["net_pnl_r"] + s["late_half"]["net_pnl_r"], reverse=True)
            best = survivors[0]
            print(f"  CONFIRMED EDGE — best surviving combo: {best['params']}")
            print(f"    Early half: {best['early_half']['total_trades']} trades, "
                  f"{best['early_half']['win_rate']}% WR, {best['early_half']['net_pnl_r']}R")
            print(f"    Late half:  {best['late_half']['total_trades']} trades, "
                  f"{best['late_half']['win_rate']}% WR, {best['late_half']['net_pnl_r']}R")
            all_results[family] = {"survivors": survivors, "best": best}

    # ── Cross-family comparison ──────────────────────────────────────
    print(f"\n{'-'*70}\n{pair} — Cross-family comparison\n{'-'*70}")
    scored = []
    for family, result in all_results.items():
        if result.get("best"):
            b = result["best"]
            combined_r = b["early_half"]["net_pnl_r"] + b["late_half"]["net_pnl_r"]
            scored.append((family, combined_r, b["params"]))
    if scored:
        scored.sort(key=lambda x: x[1], reverse=True)
        for family, combined_r, params in scored:
            print(f"  {family:22s} {combined_r:+.2f}R combined (both halves)  params={params}")
        print(f"\n  WINNER for {pair}: {scored[0][0]}")
    else:
        print(f"  No family produced a walk-forward-confirmed edge for {pair} at {interval}. "
              f"This is a valid, honest result -- report it as-is, don't force a pick.")

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tess unified strategy backtest harness")
    parser.add_argument("--pair", required=True, help="e.g. ETH/USDT")
    parser.add_argument("--family", default="all",
                         choices=list(FAMILY_MODULES.keys()) + ["all"])
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--bars", type=int, default=6000)
    parser.add_argument("--top_n", type=int, default=10)
    parser.add_argument("--max_combos", type=int, default=200)
    args = parser.parse_args()

    families = list(FAMILY_MODULES.keys()) if args.family == "all" else [args.family]
    run_pair(args.pair, families, interval=args.interval, total_bars=args.bars,
              top_n=args.top_n, max_combos=args.max_combos)
