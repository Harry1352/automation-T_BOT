# ==============================================================================
# TESS — price_action/strategy.py (v1.0)
# ==============================================================================
# Family 2 of 4. Every pattern below qualifies ONLY by numeric swing-point
# geometry + ATR-relative tolerance -- nothing is detected by shape
# resemblance. This file implements the highest-conviction, most clearly
# definable patterns from the locked spec first (double/triple top-bottom,
# H&S/inverse, triangles, rectangle, flags) rather than faking geometric
# checks for every pattern named in the PDF. Rounding top/bottom, V-top/
# V-bottom, pennant, cup & handle, and rising/falling wedge are NOT yet
# implemented -- flagged honestly as Phase 2 (see bottom of file) rather
# than shipped as broken/unverified detectors.
#
# Output convention matches base.py / technical_analysis: Signal,
# Signal_Type, Stop_Loss, Take_Profit columns.
# Exit mechanism is STRUCTURAL, not fixed R:R: SL beyond the pattern zone,
# TP at the next opposing S/R zone (falls back to ATR*R:R if no zone
# exists far enough away to be a sane target).
# ==============================================================================

import numpy as np
import pandas as pd

from _shared.indicators import ema, calc_atr, calc_adx
from _shared.swing_detector import find_swing_points, get_swing_sequence, swings_within_tolerance
from _shared.sr_zones import build_sr_zones, nearest_zone, is_ema200_confluence
from _shared.candlestick_patterns import scan_all_patterns, PATTERN_WEIGHTS

SL_ATR_BUFFER_DEFAULT = 0.5   # SL sits this many extra ATRs beyond the pattern zone
MIN_ZONE_DISTANCE_ATR = 1.5   # a TP zone closer than this isn't worth targeting -> fallback to R:R


def _fit_trendline(points):
    """points: list of (x_index, price). Returns (slope, intercept) via
    least-squares fit. Needs >=2 points."""
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    slope, intercept = np.polyfit(xs, ys, 1)
    return slope, intercept


# ══════════════════════════════════════════════════════════════════════════
# Pattern detectors — each returns a dict describing the pattern if found
# at/near bar `i`, else None. `df` must already have swing_high/swing_low
# columns (run find_swing_points first).
# ══════════════════════════════════════════════════════════════════════════

def detect_double_top(df, i, tolerance_pct, atr):
    highs = get_swing_sequence(df, "high", count=2, up_to_index=i)
    if len(highs) < 2:
        return None
    (i1, p1), (i2, p2) = highs
    if not swings_within_tolerance(p1, p2, tolerance_pct):
        return None
    lows_between = get_swing_sequence(df, "low", count=1, up_to_index=i2)
    if not lows_between or lows_between[0][0] <= i1:
        return None
    neckline = lows_between[0][1]
    close = df["close"].iloc[i]
    if close < neckline - 0.1 * atr:
        return {"pattern": "Double Top", "direction": -1, "zone_price": max(p1, p2), "neckline": neckline}
    return None


def detect_double_bottom(df, i, tolerance_pct, atr):
    lows = get_swing_sequence(df, "low", count=2, up_to_index=i)
    if len(lows) < 2:
        return None
    (i1, p1), (i2, p2) = lows
    if not swings_within_tolerance(p1, p2, tolerance_pct):
        return None
    highs_between = get_swing_sequence(df, "high", count=1, up_to_index=i2)
    if not highs_between or highs_between[0][0] <= i1:
        return None
    neckline = highs_between[0][1]
    close = df["close"].iloc[i]
    if close > neckline + 0.1 * atr:
        return {"pattern": "Double Bottom", "direction": 1, "zone_price": min(p1, p2), "neckline": neckline}
    return None


def detect_triple_top(df, i, tolerance_pct, atr):
    highs = get_swing_sequence(df, "high", count=3, up_to_index=i)
    if len(highs) < 3:
        return None
    prices = [p for _, p in highs]
    if not (swings_within_tolerance(prices[0], prices[1], tolerance_pct)
            and swings_within_tolerance(prices[1], prices[2], tolerance_pct)):
        return None
    lows = get_swing_sequence(df, "low", count=2, up_to_index=highs[-1][0])
    if len(lows) < 2:
        return None
    neckline = min(p for _, p in lows)
    close = df["close"].iloc[i]
    if close < neckline - 0.1 * atr:
        return {"pattern": "Triple Top", "direction": -1, "zone_price": max(prices), "neckline": neckline}
    return None


def detect_triple_bottom(df, i, tolerance_pct, atr):
    lows = get_swing_sequence(df, "low", count=3, up_to_index=i)
    if len(lows) < 3:
        return None
    prices = [p for _, p in lows]
    if not (swings_within_tolerance(prices[0], prices[1], tolerance_pct)
            and swings_within_tolerance(prices[1], prices[2], tolerance_pct)):
        return None
    highs = get_swing_sequence(df, "high", count=2, up_to_index=lows[-1][0])
    if len(highs) < 2:
        return None
    neckline = max(p for _, p in highs)
    close = df["close"].iloc[i]
    if close > neckline + 0.1 * atr:
        return {"pattern": "Triple Bottom", "direction": 1, "zone_price": min(prices), "neckline": neckline}
    return None


def detect_head_and_shoulders(df, i, tolerance_pct, atr):
    highs = get_swing_sequence(df, "high", count=3, up_to_index=i)
    if len(highs) < 3:
        return None
    (li, lp), (hi_, hp), (ri, rp) = highs
    if not (hp > lp and hp > rp and swings_within_tolerance(lp, rp, tolerance_pct)):
        return None
    lows = get_swing_sequence(df, "low", count=2, up_to_index=ri)
    lows = [(idx, p) for idx, p in lows if li < idx < ri]
    if len(lows) < 2:
        # fall back: take any 2 confirmed lows between shoulders
        lows = get_swing_sequence(df, "low", count=2, up_to_index=ri)
        if len(lows) < 2:
            return None
    slope, intercept = _fit_trendline(lows)
    neckline_at_i = slope * i + intercept
    close = df["close"].iloc[i]
    if close < neckline_at_i - 0.1 * atr:
        return {"pattern": "Head & Shoulders", "direction": -1, "zone_price": hp, "neckline": neckline_at_i}
    return None


def detect_inverse_head_and_shoulders(df, i, tolerance_pct, atr):
    lows = get_swing_sequence(df, "low", count=3, up_to_index=i)
    if len(lows) < 3:
        return None
    (li, lp), (hi_, hp), (ri, rp) = lows
    if not (hp < lp and hp < rp and swings_within_tolerance(lp, rp, tolerance_pct)):
        return None
    highs = get_swing_sequence(df, "high", count=2, up_to_index=ri)
    highs = [(idx, p) for idx, p in highs if li < idx < ri]
    if len(highs) < 2:
        highs = get_swing_sequence(df, "high", count=2, up_to_index=ri)
        if len(highs) < 2:
            return None
    slope, intercept = _fit_trendline(highs)
    neckline_at_i = slope * i + intercept
    close = df["close"].iloc[i]
    if close > neckline_at_i + 0.1 * atr:
        return {"pattern": "Inverse Head & Shoulders", "direction": 1, "zone_price": hp, "neckline": neckline_at_i}
    return None


def detect_triangle(df, i, atr, flat_slope_atr_thresh=0.02, lookback_swings=3):
    """Fits trendlines through the last N swing highs and N swing lows.
    Classifies as ascending (flat resistance + rising support), descending
    (falling resistance + flat support), or symmetrical (both converging,
    opposite-signed slopes). Breakout direction = whichever line the close
    breaks."""
    highs = get_swing_sequence(df, "high", count=lookback_swings, up_to_index=i)
    lows = get_swing_sequence(df, "low", count=lookback_swings, up_to_index=i)
    if len(highs) < 2 or len(lows) < 2:
        return None

    slope_h, intercept_h = _fit_trendline(highs)
    slope_l, intercept_l = _fit_trendline(lows)
    resistance_at_i = slope_h * i + intercept_h
    support_at_i = slope_l * i + intercept_l

    flat_thresh = flat_slope_atr_thresh * atr
    res_flat = abs(slope_h) <= flat_thresh
    sup_flat = abs(slope_l) <= flat_thresh

    if res_flat and slope_l > flat_thresh:
        tri_type = "Ascending Triangle"
    elif sup_flat and slope_h < -flat_thresh:
        tri_type = "Descending Triangle"
    elif slope_h < -flat_thresh and slope_l > flat_thresh:
        tri_type = "Symmetrical Triangle"
    else:
        return None

    close = df["close"].iloc[i]
    if close > resistance_at_i + 0.1 * atr:
        return {"pattern": tri_type, "direction": 1, "zone_price": resistance_at_i, "neckline": resistance_at_i}
    if close < support_at_i - 0.1 * atr:
        return {"pattern": tri_type, "direction": -1, "zone_price": support_at_i, "neckline": support_at_i}
    return None


def detect_rectangle(df, i, atr, flat_slope_atr_thresh=0.015, lookback_swings=3):
    highs = get_swing_sequence(df, "high", count=lookback_swings, up_to_index=i)
    lows = get_swing_sequence(df, "low", count=lookback_swings, up_to_index=i)
    if len(highs) < 2 or len(lows) < 2:
        return None
    slope_h, intercept_h = _fit_trendline(highs)
    slope_l, intercept_l = _fit_trendline(lows)
    flat_thresh = flat_slope_atr_thresh * atr
    if abs(slope_h) > flat_thresh or abs(slope_l) > flat_thresh:
        return None
    resistance_at_i = slope_h * i + intercept_h
    support_at_i = slope_l * i + intercept_l
    close = df["close"].iloc[i]
    if close > resistance_at_i + 0.1 * atr:
        return {"pattern": "Rectangle Breakout", "direction": 1, "zone_price": resistance_at_i, "neckline": resistance_at_i}
    if close < support_at_i - 0.1 * atr:
        return {"pattern": "Rectangle Breakdown", "direction": -1, "zone_price": support_at_i, "neckline": support_at_i}
    return None


def detect_flag(df, i, atr, impulse_lookback=20, impulse_atr_mult=3.0,
                 flag_lookback=10, flag_max_depth_atr=2.5):
    """Bull/Bear flag: a sharp impulsive move (> impulse_atr_mult * ATR
    over impulse_lookback bars) followed by a tight, shallow counter-trend
    channel (flag_lookback bars, depth <= flag_max_depth_atr). Breakout =
    close beyond the flag channel in the impulse direction."""
    if i < impulse_lookback + flag_lookback:
        return None

    impulse_start = i - flag_lookback - impulse_lookback
    impulse_end = i - flag_lookback
    impulse_move = df["close"].iloc[impulse_end] - df["close"].iloc[impulse_start]
    if abs(impulse_move) < impulse_atr_mult * atr:
        return None

    flag_slice = df.iloc[impulse_end:i]
    flag_high = flag_slice["high"].max()
    flag_low = flag_slice["low"].min()
    flag_depth = flag_high - flag_low
    if flag_depth > flag_max_depth_atr * atr:
        return None

    close = df["close"].iloc[i]
    if impulse_move > 0 and close > flag_high + 0.1 * atr:
        return {"pattern": "Bull Flag", "direction": 1, "zone_price": flag_high, "neckline": flag_high}
    if impulse_move < 0 and close < flag_low - 0.1 * atr:
        return {"pattern": "Bear Flag", "direction": -1, "zone_price": flag_low, "neckline": flag_low}
    return None


PATTERN_DETECTORS = [
    detect_double_top, detect_double_bottom,
    detect_triple_top, detect_triple_bottom,
    detect_head_and_shoulders, detect_inverse_head_and_shoulders,
]
# triangle/rectangle/flag take extra kwargs, called separately in the main loop


# ══════════════════════════════════════════════════════════════════════════
# Main strategy function
# ══════════════════════════════════════════════════════════════════════════

def implement_price_action_strategy(
    df: pd.DataFrame,
    reward_risk_ratio: float,
    adx_threshold: float = 0,          # 0 = ADX gate off (price action doesn't require it by default)
    swing_n: int = 3,
    pattern_tolerance_pct: float = 0.01,
    require_candle_confirmation: bool = True,
    require_ema_confluence: bool = False,
    sl_buffer_atr: float = SL_ATR_BUFFER_DEFAULT,
    signal_cooldown_bars: int = 5,
) -> pd.DataFrame:
    df = df.copy()
    df["EMA_200"] = ema(df["close"], 200)
    df["ATR"] = calc_atr(df)
    df["ADX"] = calc_adx(df)
    df = find_swing_points(df, n=swing_n)

    candle_flags = scan_all_patterns(df, df["ATR"]) if require_candle_confirmation else None

    df["Signal"] = 0
    df["Signal_Type"] = "None"
    df["Pattern_Zone"] = np.nan
    df["Pattern_Neckline"] = np.nan

    last_signal_bar = -10**9
    min_bar = max(swing_n * 8, 60)  # need real swing history before patterns are meaningful

    for i in range(min_bar, len(df)):
        if i - last_signal_bar < signal_cooldown_bars:
            continue

        atr = df["ATR"].iloc[i]
        if pd.isna(atr) or atr == 0:
            continue

        hit = None
        for detector in PATTERN_DETECTORS:
            hit = detector(df, i, pattern_tolerance_pct, atr)
            if hit:
                break
        if hit is None:
            hit = detect_triangle(df, i, atr)
        if hit is None:
            hit = detect_rectangle(df, i, atr)
        if hit is None:
            hit = detect_flag(df, i, atr)
        if hit is None:
            continue

        if adx_threshold > 0 and df["ADX"].iloc[i] <= adx_threshold:
            continue

        if require_candle_confirmation:
            row_flags = candle_flags.iloc[i]
            confirmed = (
                (hit["direction"] == 1 and (row_flags["bullish_engulfing"] or row_flags["hammer"]
                 or row_flags["morning_star"] or row_flags["three_white_soldiers"]))
                or
                (hit["direction"] == -1 and (row_flags["bearish_engulfing"] or row_flags["inverted_hammer"]
                 or row_flags["evening_star"] or row_flags["three_black_crows"]))
            )
            if not confirmed:
                continue

        if require_ema_confluence:
            ema200_val = df["EMA_200"].iloc[i]
            if pd.isna(ema200_val) or not is_ema200_confluence(hit["zone_price"], ema200_val, tolerance_pct=0.01):
                continue

        df.iloc[i, df.columns.get_loc("Signal")] = hit["direction"]
        df.iloc[i, df.columns.get_loc("Signal_Type")] = hit["pattern"]
        df.iloc[i, df.columns.get_loc("Pattern_Zone")] = hit["zone_price"]
        df.iloc[i, df.columns.get_loc("Pattern_Neckline")] = hit["neckline"]
        last_signal_bar = i

    # ── Structural exits: SL beyond the pattern zone, TP at next opposing
    # S/R zone, falling back to ATR*R:R if no zone is far enough away ─────
    zones = build_sr_zones(df, min_touches=2)

    df["Stop_Loss"] = np.nan
    df["Take_Profit"] = np.nan

    for i in df.index[df["Signal"] != 0]:
        pos = df.index.get_loc(i)
        direction = df.loc[i, "Signal"]
        close = df.loc[i, "close"]
        atr = df.loc[i, "ATR"]
        zone_price = df.loc[i, "Pattern_Zone"]

        if direction == 1:
            sl = zone_price - sl_buffer_atr * atr
            target_zone = nearest_zone(zones, close, direction="above")
            if target_zone and (target_zone["price"] - close) >= MIN_ZONE_DISTANCE_ATR * atr:
                tp = target_zone["price"]
            else:
                tp = close + atr * reward_risk_ratio
        else:
            sl = zone_price + sl_buffer_atr * atr
            target_zone = nearest_zone(zones, close, direction="below")
            if target_zone and (close - target_zone["price"]) >= MIN_ZONE_DISTANCE_ATR * atr:
                tp = target_zone["price"]
            else:
                tp = close - atr * reward_risk_ratio

        df.loc[i, "Stop_Loss"] = sl
        df.loc[i, "Take_Profit"] = tp

    return df


SWEEP_GRID = {
    "adx_threshold": [0, 14, 20],
    "reward_risk_ratio": [2, 3, 5, 7],
    "swing_n": [2, 3, 5],
    "pattern_tolerance_pct": [0.005, 0.01, 0.02],
}

# ══════════════════════════════════════════════════════════════════════════
# PHASE 2 — not yet implemented (flagged honestly, not faked):
#   Rounding Top / Rounding Bottom (needs curve-fit regression classifier)
#   V-Top / V-Bottom (needs speed-of-move differentiation from double top/bottom)
#   Pennant (flag's narrower cousin -- needs converging-channel variant of detect_flag)
#   Cup and Handle (needs rounded-base + shallow-handle two-stage detector)
#   Rising Wedge / Falling Wedge (needs same-direction-slope trendline classifier,
#     context-dependent on prevailing trend direction)
# ══════════════════════════════════════════════════════════════════════════
