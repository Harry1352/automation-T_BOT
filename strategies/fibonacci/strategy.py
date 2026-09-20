# ==============================================================================
# TESS — fibonacci/strategy.py (v1.0)
# ==============================================================================
# Family 3 of 4. Auto-detects swing high->low legs, enters on retracement
# into the Golden Zone (50%-61.8%), trend-filtered by EMA200, confirmed by
# a rejection candle. TP via extension levels (structural exit, not fixed
# R:R), with Booming Bulls' invalidation rule: a clean break past 61.8%
# without slowing = trend reversal, not a pullback -- so we do NOT enter
# once price has already sliced through the zone.
#
# Output convention matches base.py / technical_analysis: Signal,
# Signal_Type, Stop_Loss, Take_Profit columns.
# ==============================================================================

import numpy as np
import pandas as pd

from _shared.indicators import ema, calc_atr, calc_adx
from _shared.swing_detector import find_swing_points, get_last_confirmed_swing
from _shared.sr_zones import build_sr_zones
from _shared.candlestick_patterns import (
    is_hammer, is_inverted_hammer, is_bullish_engulfing, is_bearish_engulfing,
)

GOLDEN_ZONE_LOW = 0.50
GOLDEN_ZONE_HIGH = 0.618
INVALIDATION_LEVEL = 0.618   # clean break past this without a rejection = reversal, not pullback
EXTENSION_LEVELS = [1.272, 1.618, 2.618]


def _fib_levels(swing_low_price, swing_high_price):
    """Returns retracement levels 0..1 mapped onto the leg, plus extension
    levels beyond 1.0, for an UP leg (low->high). For a DOWN leg the same
    math applies mirrored -- see _fib_levels_down."""
    span = swing_high_price - swing_low_price
    retr = {lvl: swing_high_price - span * lvl for lvl in [0.382, 0.5, 0.618, 1.0]}
    ext = {lvl: swing_high_price + span * (lvl - 1.0) for lvl in EXTENSION_LEVELS}
    return retr, ext


def _fib_levels_down(swing_high_price, swing_low_price):
    span = swing_high_price - swing_low_price
    retr = {lvl: swing_low_price + span * lvl for lvl in [0.382, 0.5, 0.618, 1.0]}
    ext = {lvl: swing_low_price - span * (lvl - 1.0) for lvl in EXTENSION_LEVELS}
    return retr, ext


def _pick_extension_target(ext: dict, reward_risk_ratio: float) -> float:
    """Maps the swept reward_risk_ratio onto which fib extension level is
    used as TP -- keeps R:R meaningfully swept here too, rather than being
    a declared-but-ignored parameter. Lower RR asks for the closer/safer
    1.272 extension; higher RR reaches for 2.618."""
    if reward_risk_ratio <= 2:
        return ext[1.272]
    elif reward_risk_ratio <= 3:
        return ext[1.618]
    else:
        return ext[2.618]


def implement_fibonacci_strategy(
    df: pd.DataFrame,
    reward_risk_ratio: float,
    adx_threshold: float = 0,
    swing_n: int = 3,
    require_candle_confirmation: bool = True,
    require_sr_confluence: bool = False,
    sl_buffer_atr: float = 0.3,
    signal_cooldown_bars: int = 5,
) -> pd.DataFrame:
    df = df.copy()
    df["EMA_200"] = ema(df["close"], 200)
    df["ATR"] = calc_atr(df)
    df["ADX"] = calc_adx(df)
    df = find_swing_points(df, n=swing_n)

    df["Signal"] = 0
    df["Signal_Type"] = "None"
    df["Stop_Loss"] = np.nan
    df["Take_Profit"] = np.nan

    zones = build_sr_zones(df, min_touches=2) if require_sr_confluence else []

    min_bar = max(swing_n * 8, 60)
    last_signal_bar = -10**9
    # Track whether the current active leg has already been "invalidated"
    # (price sliced past 61.8% without a rejection) so we don't re-enter
    # on the same broken leg later in the same trend run.
    invalidated_leg_key = None

    for i in range(min_bar, len(df)):
        if i - last_signal_bar < signal_cooldown_bars:
            continue

        atr = df["ATR"].iloc[i]
        if pd.isna(atr) or atr == 0:
            continue

        close = df["close"].iloc[i]
        ema200 = df["EMA_200"].iloc[i]
        if pd.isna(ema200):
            continue

        uptrend = close > ema200
        downtrend = close < ema200

        # Find the most recent completed swing leg relevant to this trend:
        # uptrend -> last swing low then the swing high that followed it
        # downtrend -> last swing high then the swing low that followed it
        if uptrend:
            low_pos, low_price = get_last_confirmed_swing(df, i, "low")
            if low_pos is None:
                continue
            high_pos, high_price = get_last_confirmed_swing(df, i, "high")
            if high_pos is None or high_pos <= low_pos:
                continue  # need a high AFTER the low to define a completed up-leg
            leg_key = (low_pos, high_pos)
            if leg_key == invalidated_leg_key:
                continue
            retr, ext = _fib_levels(low_price, high_price)
            zone_top, zone_bottom = retr[GOLDEN_ZONE_LOW], retr[GOLDEN_ZONE_HIGH]
            invalidation_price = retr[INVALIDATION_LEVEL]

            if close < invalidation_price - 0.1 * atr:
                invalidated_leg_key = leg_key  # sliced clean through -> reversal, not pullback
                continue

            in_golden_zone = zone_bottom <= close <= zone_top
            if not in_golden_zone:
                continue

            if require_candle_confirmation:
                row = df.iloc[i]
                if not (is_hammer(row, atr) or is_bullish_engulfing(df, i)):
                    continue

            if require_sr_confluence:
                overlap = any(swings_close(z["price"], zone_top, zone_bottom) for z in zones)
                if not overlap:
                    continue

            df.iloc[i, df.columns.get_loc("Signal")] = 1
            df.iloc[i, df.columns.get_loc("Signal_Type")] = "Fib Golden Zone Long"
            sl = low_price - sl_buffer_atr * atr
            tp = _pick_extension_target(ext, reward_risk_ratio)
            df.iloc[i, df.columns.get_loc("Stop_Loss")] = sl
            df.iloc[i, df.columns.get_loc("Take_Profit")] = tp
            last_signal_bar = i

        elif downtrend:
            high_pos, high_price = get_last_confirmed_swing(df, i, "high")
            if high_pos is None:
                continue
            low_pos, low_price = get_last_confirmed_swing(df, i, "low")
            if low_pos is None or low_pos <= high_pos:
                continue
            leg_key = (high_pos, low_pos)
            if leg_key == invalidated_leg_key:
                continue
            retr, ext = _fib_levels_down(high_price, low_price)
            zone_bottom, zone_top = retr[GOLDEN_ZONE_LOW], retr[GOLDEN_ZONE_HIGH]
            invalidation_price = retr[INVALIDATION_LEVEL]

            if close > invalidation_price + 0.1 * atr:
                invalidated_leg_key = leg_key
                continue

            in_golden_zone = zone_bottom <= close <= zone_top
            if not in_golden_zone:
                continue

            if require_candle_confirmation:
                row = df.iloc[i]
                if not (is_inverted_hammer(row, atr) or is_bearish_engulfing(df, i)):
                    continue

            df.iloc[i, df.columns.get_loc("Signal")] = -1
            df.iloc[i, df.columns.get_loc("Signal_Type")] = "Fib Golden Zone Short"
            sl = high_price + sl_buffer_atr * atr
            tp = _pick_extension_target(ext, reward_risk_ratio)
            df.iloc[i, df.columns.get_loc("Stop_Loss")] = sl
            df.iloc[i, df.columns.get_loc("Take_Profit")] = tp
            last_signal_bar = i

    return df


def swings_close(price_a, band_low, band_high, buffer_pct=0.003):
    """True if price_a sits within [band_low, band_high] expanded slightly,
    used for the optional S/R confluence check."""
    span = band_high - band_low
    buf = max(span * 0.5, band_high * buffer_pct)
    return (band_low - buf) <= price_a <= (band_high + buf)


SWEEP_GRID = {
    "adx_threshold": [0, 14, 20],
    "reward_risk_ratio": [2, 3, 5, 7],   # note: TP is extension-based here, R:R mainly informs SL-relative sizing checks
    "swing_n": [2, 3, 5],
    "require_sr_confluence": [False, True],
}
