# ==============================================================================
# TESS — strategies/family_dispatch.py (v1.0)
# ==============================================================================
# Connects the 4 new strategy families (technical_analysis, price_action,
# fibonacci, price_action_smc) to scanner.py WITHOUT modifying base.py,
# bitcoin.py, ethereum.py, solana.py, xaut.py, or __init__.py's existing
# per-coin dispatch. The current live system keeps working completely
# unchanged if you never touch STRATEGY_FAMILY_BY_PAIR below.
#
# Why a separate file instead of editing __init__.py directly: __init__.py
# is the dispatcher your live scanner.py already imports and depends on
# every single scan. Editing it blind, without the ability to re-verify
# scanner.py's exact current call sites, risks silently breaking live
# paper trading. This file is purely additive -- scanner.py opts into it
# with two small, explicit changes (below), and can be reverted by
# removing those two lines with zero risk to the existing pipeline.
#
# WORKFLOW:
#   1. Run backtest_harness.py per pair, per family, on your machine.
#   2. Only after a family WALK-FORWARD VALIDATES for a given pair (same
#      bar as BTC/SOL already cleared under the old system), set that
#      pair's entry below to the winning family name.
#   3. Anything not listed here, or listed as "technical_analysis_live",
#      keeps using the exact current base.py system, completely untouched.
# ==============================================================================

import pandas as pd

# ── Set this AFTER walk-forward validation confirms a real edge ───────────
# Values: "technical_analysis_live" (default -- current base.py system,
# UNCHANGED), or one of: "technical_analysis", "price_action", "fibonacci",
# "price_action_smc" (the new families, once validated).
STRATEGY_FAMILY_BY_PAIR = {
    "ETH/USDT": "technical_analysis_live",
    "SOL/USDT": "technical_analysis_live",
    "BTC/USDT": "technical_analysis_live",
    "XAUT/USDT": "technical_analysis_live",
}

# Per-family swept parameters that WON validation for a pair go here once
# known -- mirrors the exact pattern bitcoin.py/solana.py already use for
# ADX/RR, just extended to hold a whole params dict instead of two values.
VALIDATED_PARAMS_BY_PAIR = {
    # Example once you have a real result:
    # "ETH/USDT": {"adx_threshold": 20, "reward_risk_ratio": 3, "swing_n": 3, "pattern_tolerance_pct": 0.01},
}

_FAMILY_MODULES = {
    "technical_analysis": ("technical_analysis.strategy", "implement_technical_analysis_strategy"),
    "price_action": ("price_action.strategy", "implement_price_action_strategy"),
    "fibonacci": ("fibonacci.strategy", "implement_fibonacci_strategy"),
    "price_action_smc": ("price_action_smc.strategy", "implement_price_action_smc_strategy"),
}


def get_signal_for_pair(pair: str, candles: pd.DataFrame, legacy_snapshot_builder=None):
    """
    Single entry point scanner.py calls. Returns whatever base.py's
    build_market_snapshot() currently returns for pairs still on
    "technical_analysis_live" (byte-for-byte the existing live behavior,
    via legacy_snapshot_builder which you pass in as strategies.build_market_snapshot),
    or runs the new family's strategy.py for pairs that have been
    switched over post-validation.
    """
    family = STRATEGY_FAMILY_BY_PAIR.get(pair, "technical_analysis_live")

    if family == "technical_analysis_live":
        if legacy_snapshot_builder is None:
            raise ValueError(
                "family_dispatch: pair is set to technical_analysis_live but no "
                "legacy_snapshot_builder was passed in. Pass strategies.build_market_snapshot."
            )
        return legacy_snapshot_builder(pair, candles)

    if family not in _FAMILY_MODULES:
        raise ValueError(f"family_dispatch: unknown family {family!r} for pair {pair!r}")

    import importlib
    module_path, fn_name = _FAMILY_MODULES[family]
    module = importlib.import_module(module_path)
    strategy_fn = getattr(module, fn_name)

    params = VALIDATED_PARAMS_BY_PAIR.get(pair)
    if not params:
        raise ValueError(
            f"family_dispatch: {pair} is set to family {family!r} but has no entry in "
            f"VALIDATED_PARAMS_BY_PAIR. Do not go live on a family/pair combo that hasn't "
            f"walk-forward validated -- run backtest_harness.py first."
        )

    out = strategy_fn(candles.copy(), **params)
    last = out.iloc[-1]
    return {
        "pair": pair,
        "family": family,
        "technical_signal": int(last["Signal"]),
        "signal_type": last.get("Signal_Type", "None"),
        "suggested_sl": last.get("Stop_Loss"),
        "suggested_tp": last.get("Take_Profit"),
    }
