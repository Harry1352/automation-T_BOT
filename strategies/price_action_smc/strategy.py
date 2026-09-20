# ==============================================================================
# TESS — price_action_smc/strategy.py (v1.0)
# ==============================================================================
# Family 4 of 4 -- highest complexity, highest overfitting risk, held to
# the strictest validation bar of the four (per the locked spec).
#
# Components:
#   - Market structure: Break of Structure (BOS) / Change of Character (CHoCH)
#   - Order blocks: last opposing candle before an impulsive move
#   - Fair value gaps: 3-candle imbalance
#   - Liquidity sweeps: wick beyond a recent swing extreme, closes back inside
#   - Previous Day High/Low (PDH/PDL) as liquidity reference
#   - Correlated-asset divergence: crypto-adapted equivalent of Booming
#     Bulls' Nifty/Bank Nifty SMT -- requires a SECOND asset's OHLCV as
#     input (e.g. BTC candles when trading ETH). Optional: strategy still
#     runs without it, just skips that confluence layer.
#
# Output convention matches base.py: Signal, Signal_Type, Stop_Loss,
# Take_Profit columns. SL beyond the OB/sweep wick, TP at the next
# liquidity pool (opposing swing extreme / PDH-PDL), same structural-exit
# philosophy as price_action/.
# ==============================================================================

import numpy as np
import pandas as pd

from _shared.indicators import calc_atr
from _shared.swing_detector import find_swing_points, get_swing_sequence

IMPULSE_ATR_MULT_DEFAULT = 2.0
FVG_MIN_GAP_ATR = 0.15


# ══════════════════════════════════════════════════════════════════════════
# Market structure: BOS / CHoCH
# ══════════════════════════════════════════════════════════════════════════

def detect_structure_at(df: pd.DataFrame, i: int, trend_state: str):
    """
    trend_state: "bullish", "bearish", or "neutral" -- the structure state
    going INTO bar i (from the previous bar's classification). Returns
    (event, new_trend_state):
      event in {"BOS_bullish", "BOS_bearish", "CHoCH_bullish", "CHoCH_bearish", None}

    BOS (Break of Structure) = trend continues: a new higher high in an
    uptrend, or new lower low in a downtrend.
    CHoCH (Change of Character) = trend flips: price breaks the opposite
    direction's most recent swing, e.g. a downtrend's rally finally closes
    above the last lower-high -- first sign of a reversal.
    """
    close = df["close"].iloc[i]
    last_high = get_swing_sequence(df, "high", count=1, up_to_index=i)
    last_low = get_swing_sequence(df, "low", count=1, up_to_index=i)
    last_high_price = last_high[0][1] if last_high else None
    last_low_price = last_low[0][1] if last_low else None

    if trend_state == "bullish":
        if last_high_price is not None and close > last_high_price:
            return "BOS_bullish", "bullish"
        if last_low_price is not None and close < last_low_price:
            return "CHoCH_bearish", "bearish"
    elif trend_state == "bearish":
        if last_low_price is not None and close < last_low_price:
            return "BOS_bearish", "bearish"
        if last_high_price is not None and close > last_high_price:
            return "CHoCH_bullish", "bullish"
    else:  # neutral -- establish initial state from whichever breaks first
        if last_high_price is not None and close > last_high_price:
            return "CHoCH_bullish", "bullish"
        if last_low_price is not None and close < last_low_price:
            return "CHoCH_bearish", "bearish"

    return None, trend_state


# ══════════════════════════════════════════════════════════════════════════
# Order blocks: last opposing candle before an impulsive move
# ══════════════════════════════════════════════════════════════════════════

def find_order_block(df: pd.DataFrame, impulse_end_i: int, direction: int, atr: float,
                      impulse_atr_mult: float = IMPULSE_ATR_MULT_DEFAULT, lookback: int = 10):
    """
    direction: 1 (bullish impulse -> look for the last BEARISH candle
    before it) or -1 (bearish impulse -> last BULLISH candle before it).
    Confirms the move from that candle to impulse_end_i is a real impulse
    (>= impulse_atr_mult * atr). Returns (ob_low, ob_high, ob_index) or
    None if no qualifying candle/impulse found in `lookback` bars.
    """
    start = max(0, impulse_end_i - lookback)
    for j in range(impulse_end_i - 1, start - 1, -1):
        row = df.iloc[j]
        is_opposing = (row["close"] < row["open"]) if direction == 1 else (row["close"] > row["open"])
        if not is_opposing:
            continue
        move = df["close"].iloc[impulse_end_i] - df["close"].iloc[j]
        if direction == 1 and move >= impulse_atr_mult * atr:
            return row["low"], row["high"], j
        if direction == -1 and -move >= impulse_atr_mult * atr:
            return row["low"], row["high"], j
    return None


# ══════════════════════════════════════════════════════════════════════════
# Fair value gaps: 3-candle imbalance
# ══════════════════════════════════════════════════════════════════════════

def find_fair_value_gap(df: pd.DataFrame, i: int, atr: float, min_gap_atr: float = FVG_MIN_GAP_ATR):
    """
    Classic 3-candle FVG: candle[i-2].high < candle[i].low (bullish gap,
    the zone between them was never traded) or the mirror for bearish.
    Returns (gap_low, gap_high, direction) or None.
    """
    if i < 2:
        return None
    c1, c3 = df.iloc[i - 2], df.iloc[i]
    if c1["high"] < c3["low"] and (c3["low"] - c1["high"]) >= min_gap_atr * atr:
        return c1["high"], c3["low"], 1
    if c1["low"] > c3["high"] and (c1["low"] - c3["high"]) >= min_gap_atr * atr:
        return c3["high"], c1["low"], -1
    return None


# ══════════════════════════════════════════════════════════════════════════
# Liquidity sweeps: wick beyond a recent swing extreme, closes back inside
# ══════════════════════════════════════════════════════════════════════════

def is_liquidity_sweep(df: pd.DataFrame, i: int, lookback: int = 20):
    """
    Returns "bullish" (swept a low, closed back above it -- stop-hunt
    before reversal up), "bearish" (mirror), or None.
    """
    row = df.iloc[i]
    window = df.iloc[max(0, i - lookback):i]
    if window.empty:
        return None
    recent_low = window["low"].min()
    recent_high = window["high"].max()

    if row["low"] < recent_low and row["close"] > recent_low:
        return "bullish"
    if row["high"] > recent_high and row["close"] < recent_high:
        return "bearish"
    return None


# ══════════════════════════════════════════════════════════════════════════
# Previous Day High/Low
# ══════════════════════════════════════════════════════════════════════════

def calc_pdh_pdl(df: pd.DataFrame) -> pd.DataFrame:
    """Requires a DatetimeIndex. Returns columns pdh, pdl -- yesterday's
    (UTC calendar day) high/low, forward-filled across today's bars."""
    if not isinstance(df.index, pd.DatetimeIndex):
        return pd.DataFrame({"pdh": np.nan, "pdl": np.nan}, index=df.index)
    daily_high = df["high"].groupby(df.index.date).max()
    daily_low = df["low"].groupby(df.index.date).min()
    day_series = pd.Series(df.index.date, index=df.index)
    pdh = day_series.map(lambda d: daily_high.shift(1).get(d, np.nan))
    pdl = day_series.map(lambda d: daily_low.shift(1).get(d, np.nan))
    return pd.DataFrame({"pdh": pdh, "pdl": pdl}, index=df.index)


# ══════════════════════════════════════════════════════════════════════════
# Correlated-asset divergence -- crypto-adapted "SMT"
# ══════════════════════════════════════════════════════════════════════════

def detect_correlated_divergence(df_primary: pd.DataFrame, df_secondary: pd.DataFrame,
                                   i: int, lookback: int = 20):
    """
    Honest adaptation of Booming Bulls' Nifty/Bank Nifty SMT rule for
    crypto: if the primary asset (e.g. ETH) breaks its recent swing low
    but the secondary asset (e.g. BTC) does NOT confirm the same break,
    that's a bearish-structure divergence -- primary may be a trap, not a
    real breakdown (mirrors his "one index breaks, other doesn't = trap"
    rule). Requires df_secondary aligned to the same index as df_primary
    (same timeframe, same timestamps). Returns "bullish_divergence",
    "bearish_divergence", or None. Optional layer -- callers should treat
    a None secondary asset as "skip this check", not as a failure.
    """
    if df_secondary is None or i < lookback:
        return None
    try:
        prim_window = df_primary.iloc[i - lookback:i + 1]
        sec_window = df_secondary.iloc[i - lookback:i + 1]
    except Exception:
        return None
    if len(prim_window) != len(sec_window):
        return None

    prim_low_break = prim_window["low"].iloc[-1] <= prim_window["low"].iloc[:-1].min()
    sec_low_break = sec_window["low"].iloc[-1] <= sec_window["low"].iloc[:-1].min()
    if prim_low_break and not sec_low_break:
        return "bullish_divergence"  # primary faked a breakdown, secondary didn't confirm -> trap, expect reversal up

    prim_high_break = prim_window["high"].iloc[-1] >= prim_window["high"].iloc[:-1].max()
    sec_high_break = sec_window["high"].iloc[-1] >= sec_window["high"].iloc[:-1].max()
    if prim_high_break and not sec_high_break:
        return "bearish_divergence"

    return None


# ══════════════════════════════════════════════════════════════════════════
# Main strategy function
# ══════════════════════════════════════════════════════════════════════════

def implement_price_action_smc_strategy(
    df: pd.DataFrame,
    reward_risk_ratio: float,
    adx_threshold: float = 0,
    swing_n: int = 3,
    impulse_atr_mult: float = IMPULSE_ATR_MULT_DEFAULT,
    require_liquidity_sweep: bool = False,
    sl_buffer_atr: float = 0.3,
    signal_cooldown_bars: int = 5,
    df_secondary: pd.DataFrame = None,   # optional correlated asset for divergence confluence
) -> pd.DataFrame:
    df = df.copy()
    df["ATR"] = calc_atr(df)
    df = find_swing_points(df, n=swing_n)
    pdh_pdl = calc_pdh_pdl(df)
    df["PDH"] = pdh_pdl["pdh"]
    df["PDL"] = pdh_pdl["pdl"]

    df["Signal"] = 0
    df["Signal_Type"] = "None"
    df["Stop_Loss"] = np.nan
    df["Take_Profit"] = np.nan

    min_bar = max(swing_n * 8, 60)
    last_signal_bar = -10**9
    trend_state = "neutral"
    last_bos_choch_i = None
    last_bos_choch_dir = 0  # 1 = bullish event just occurred, -1 = bearish

    for i in range(min_bar, len(df)):
        atr = df["ATR"].iloc[i]
        if pd.isna(atr) or atr == 0:
            continue

        event, trend_state = detect_structure_at(df, i, trend_state)
        if event is not None:
            last_bos_choch_i = i
            last_bos_choch_dir = 1 if "bullish" in event else -1

        if i - last_signal_bar < signal_cooldown_bars:
            continue
        if last_bos_choch_i is None or (i - last_bos_choch_i) > 15:
            continue  # only act reasonably soon after a structure event, not stale history

        direction = last_bos_choch_dir
        ob = find_order_block(df, last_bos_choch_i, direction, atr, impulse_atr_mult)
        fvg = find_fair_value_gap(df, last_bos_choch_i, atr)
        sweep = is_liquidity_sweep(df, i)

        if require_liquidity_sweep and sweep != ("bullish" if direction == 1 else "bearish"):
            continue

        close = df["close"].iloc[i]
        in_ob = ob is not None and ob[0] <= close <= ob[1]
        in_fvg = fvg is not None and fvg[2] == direction and fvg[0] <= close <= fvg[1]
        if not (in_ob or in_fvg):
            continue

        # Optional correlated-asset divergence confluence -- boosts
        # conviction but is never a hard requirement (df_secondary may be
        # absent entirely).
        divergence_confluence = False
        if df_secondary is not None:
            div = detect_correlated_divergence(df, df_secondary, i)
            wants = "bullish_divergence" if direction == 1 else "bearish_divergence"
            divergence_confluence = (div == wants)

        if adx_threshold > 0:
            from _shared.indicators import calc_adx
            adx_val = calc_adx(df).iloc[i]
            if pd.isna(adx_val) or adx_val <= adx_threshold:
                continue

        zone_low, zone_high = (ob[0], ob[1]) if in_ob else (fvg[0], fvg[1])

        if direction == 1:
            sl = zone_low - sl_buffer_atr * atr
            liquidity_targets = [v for v in [df["PDH"].iloc[i]] if not pd.isna(v) and v > close]
            highs = get_swing_sequence(df, "high", count=1, up_to_index=i)
            if highs:
                liquidity_targets.append(highs[0][1])
            tp = max(liquidity_targets) if liquidity_targets else close + atr * reward_risk_ratio
            sig_type = "SMC Bullish OB/FVG Entry" + (" +Div" if divergence_confluence else "")
        else:
            sl = zone_high + sl_buffer_atr * atr
            liquidity_targets = [v for v in [df["PDL"].iloc[i]] if not pd.isna(v) and v < close]
            lows = get_swing_sequence(df, "low", count=1, up_to_index=i)
            if lows:
                liquidity_targets.append(lows[0][1])
            tp = min(liquidity_targets) if liquidity_targets else close - atr * reward_risk_ratio
            sig_type = "SMC Bearish OB/FVG Entry" + (" +Div" if divergence_confluence else "")

        df.iloc[i, df.columns.get_loc("Signal")] = direction
        df.iloc[i, df.columns.get_loc("Signal_Type")] = sig_type
        df.iloc[i, df.columns.get_loc("Stop_Loss")] = sl
        df.iloc[i, df.columns.get_loc("Take_Profit")] = tp
        last_signal_bar = i

    return df


SWEEP_GRID = {
    "adx_threshold": [0, 14, 20],
    "reward_risk_ratio": [2, 3, 5, 7],
    "swing_n": [2, 3, 5],
    "impulse_atr_mult": [1.5, 2.0, 3.0],
    "require_liquidity_sweep": [False, True],
}
