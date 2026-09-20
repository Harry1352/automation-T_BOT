# ==============================================================================
# TESS — technical_analysis/strategy.py (v1.0)
# ==============================================================================
# Family 1 of 4: "The Roots" — the control group every other family is
# measured against.
#
# Two independent signal paths, each mathematically defined, combinable
# via `signal_mode`:
#
#   PATH A "ema_macd"      — Tess's current live EMA 9/20/200 regime +
#                             crossover/pullback entries, now REQUIRING
#                             MACD histogram confirmation in the same
#                             direction (filters out weak/fading crosses
#                             the old system would have taken blind).
#                             RSI divergence adds a scored bonus, not a
#                             hard requirement (divergence is rare -- a
#                             hard requirement would starve the signal).
#
#   PATH B "vwap_stochrsi" — Booming Bulls' VWAP bias + Stoch RSI timing
#                             stack: VWAP is the absolute long/short bias
#                             line, Stoch RSI crossing out of OB(>80)/
#                             OS(<20) is the entry trigger, gated by
#                             volume confirmation.
#
#   signal_mode="confluence" requires BOTH paths to agree on direction in
#   the same bar -- highest conviction, fewest signals. This mode
#   (ema_macd / vwap_stochrsi / confluence) is itself a swept dimension in
#   the backtest harness, same as ADX and R:R -- we let the walk-forward
#   evidence decide which mode actually earns its keep per coin, rather
#   than assuming "more confirmation = better" without proof.
#
# Output convention matches strategies/base.py exactly (Signal,
# Signal_Type, Stop_Loss, Take_Profit columns) so backtest_harness.py can
# walk this the same way it walks the current live system.
# ==============================================================================

import numpy as np
import pandas as pd

from _shared.indicators import (
    ema, calc_rsi, calc_atr, calc_adx, calc_macd, calc_bollinger_bands,
    is_bb_squeeze, calc_vwap, calc_stoch_rsi, calc_volume_confirmation,
    detect_rsi_divergence,
)

SL_ATR_MULT_DEFAULT = 2.5


def implement_technical_analysis_strategy(
    df: pd.DataFrame,
    adx_threshold: float,
    reward_risk_ratio: float,
    signal_mode: str = "ema_macd",      # "ema_macd" | "vwap_stochrsi" | "confluence"
    sl_atr_mult: float = SL_ATR_MULT_DEFAULT,
    require_volume_confirmation: bool = True,
    divergence_bonus: bool = True,
) -> pd.DataFrame:
    df = df.copy()

    # ── Core indicators ──────────────────────────────────────────────
    df["EMA_9"] = ema(df["close"], 9)
    df["EMA_20"] = ema(df["close"], 20)
    df["EMA_200"] = ema(df["close"], 200)
    df["RSI"] = calc_rsi(df["close"])
    df["ATR"] = calc_atr(df)
    df["ADX"] = calc_adx(df)

    macd = calc_macd(df["close"])
    df["MACD_line"] = macd["macd_line"]
    df["MACD_signal"] = macd["signal_line"]
    df["MACD_hist"] = macd["histogram"]

    bb = calc_bollinger_bands(df["close"])
    df["BB_width_pct"] = bb["bb_width_pct"]
    df["BB_squeeze"] = is_bb_squeeze(df["BB_width_pct"])

    vol_conf = calc_volume_confirmation(df["volume"])
    df["Vol_confirmed"] = vol_conf["vol_confirmed"]

    df["VWAP"] = calc_vwap(df)

    stoch_rsi = calc_stoch_rsi(df["close"])
    df["StochRSI_K"] = stoch_rsi["stoch_rsi_k"]
    df["StochRSI_D"] = stoch_rsi["stoch_rsi_d"]

    div = detect_rsi_divergence(df["close"], df["RSI"])
    df["Bullish_Divergence"] = div["bullish_divergence"]
    df["Bearish_Divergence"] = div["bearish_divergence"]

    # ── PATH A: EMA regime + crossover/pullback + MACD confirmation ───
    df["EMA_9_prev"] = df["EMA_9"].shift(1)
    df["EMA_20_prev"] = df["EMA_20"].shift(1)

    macro_bullish = (df["close"] > df["EMA_200"]) & (df["EMA_9"] > df["EMA_20"])
    macro_bearish = (df["close"] < df["EMA_200"]) & (df["EMA_9"] < df["EMA_20"])

    crossover_long = (df["EMA_9_prev"] <= df["EMA_20_prev"]) & (df["EMA_9"] > df["EMA_20"]) & (df["close"] > df["EMA_200"])
    crossover_short = (df["EMA_9_prev"] >= df["EMA_20_prev"]) & (df["EMA_9"] < df["EMA_20"]) & (df["close"] < df["EMA_200"])
    pullback_long = macro_bullish & (df["low"] <= df["EMA_9"]) & (df["close"] >= df["EMA_20"])
    pullback_short = macro_bearish & (df["high"] >= df["EMA_9"]) & (df["close"] <= df["EMA_20"])

    macd_confirms_long = df["MACD_hist"] > 0
    macd_confirms_short = df["MACD_hist"] < 0

    path_a_long = (crossover_long | pullback_long) & macd_confirms_long
    path_a_short = (crossover_short | pullback_short) & macd_confirms_short

    # ── PATH B: VWAP bias + Stoch RSI trigger + volume ─────────────────
    vwap_bias_long = df["close"] > df["VWAP"]
    vwap_bias_short = df["close"] < df["VWAP"]

    stoch_k_prev = df["StochRSI_K"].shift(1)
    stoch_cross_up_from_os = (stoch_k_prev < 20) & (df["StochRSI_K"] >= 20)
    stoch_cross_down_from_ob = (stoch_k_prev > 80) & (df["StochRSI_K"] <= 80)

    path_b_long = vwap_bias_long & stoch_cross_up_from_os
    path_b_short = vwap_bias_short & stoch_cross_down_from_ob

    if require_volume_confirmation:
        path_b_long = path_b_long & df["Vol_confirmed"]
        path_b_short = path_b_short & df["Vol_confirmed"]

    # ── Combine per signal_mode ─────────────────────────────────────
    if signal_mode == "ema_macd":
        raw_long, raw_short = path_a_long, path_a_short
        signal_type_long, signal_type_short = "EMA+MACD Long", "EMA+MACD Short"
    elif signal_mode == "vwap_stochrsi":
        raw_long, raw_short = path_b_long, path_b_short
        signal_type_long, signal_type_short = "VWAP+StochRSI Long", "VWAP+StochRSI Short"
    elif signal_mode == "confluence":
        raw_long, raw_short = path_a_long & path_b_long, path_a_short & path_b_short
        signal_type_long, signal_type_short = "Confluence Long", "Confluence Short"
    else:
        raise ValueError(f"Unknown signal_mode: {signal_mode!r} (expected ema_macd / vwap_stochrsi / confluence)")

    adx_confirmed = df["ADX"] > adx_threshold

    df["Signal"] = 0
    df.loc[raw_long & adx_confirmed, "Signal"] = 1
    df.loc[raw_short & adx_confirmed, "Signal"] = -1

    df["Signal_Type"] = "None"
    df.loc[raw_long & adx_confirmed, "Signal_Type"] = signal_type_long
    df.loc[raw_short & adx_confirmed, "Signal_Type"] = signal_type_short

    # Divergence bonus: annotate, don't gate -- lets the backtest harness
    # measure whether divergence-flagged signals actually perform better,
    # rather than assuming it and silently throwing away non-divergence
    # signals that might be just as good.
    if divergence_bonus:
        df["Divergence_Bonus"] = (
            (df["Signal"] == 1) & df["Bullish_Divergence"]
        ) | (
            (df["Signal"] == -1) & df["Bearish_Divergence"]
        )
    else:
        df["Divergence_Bonus"] = False

    # ── SL / TP (ATR-based, same mechanism as the live system) ─────────
    tp_mult = sl_atr_mult * reward_risk_ratio
    df["Stop_Loss"] = np.nan
    df["Take_Profit"] = np.nan

    df.loc[df["Signal"] == 1, "Stop_Loss"] = df["close"] - (df["ATR"] * sl_atr_mult)
    df.loc[df["Signal"] == 1, "Take_Profit"] = df["close"] + (df["ATR"] * tp_mult)
    df.loc[df["Signal"] == -1, "Stop_Loss"] = df["close"] + (df["ATR"] * sl_atr_mult)
    df.loc[df["Signal"] == -1, "Take_Profit"] = df["close"] - (df["ATR"] * tp_mult)

    df.drop(columns=["EMA_9_prev", "EMA_20_prev"], inplace=True, errors="ignore")
    return df


# Swept parameter grid for this family -- consumed by backtest_harness.py.
# adx_threshold and reward_risk_ratio are swept for every family (per the
# locked R:R policy); signal_mode is specific to this family.
SWEEP_GRID = {
    "adx_threshold": [14, 20, 25, 30],
    "reward_risk_ratio": [2, 3, 5, 7],
    "signal_mode": ["ema_macd", "vwap_stochrsi", "confluence"],
}
