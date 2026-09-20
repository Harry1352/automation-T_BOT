# ==============================================================================
# TESS — _shared/candlestick_patterns.py (v1.0)
# ==============================================================================
# Full candlestick taxonomy, per the locked strategy spec. Every pattern
# qualifies by numeric geometry only (body-to-wick ratio, ATR-relative
# sizing) -- nothing is detected by visual resemblance. Used by
# price_action/, fibonacci/ (rejection candle confirmation), and
# price_action_smc/ (order block / sweep confirmation) as the shared
# confirmation layer at any zone touch.
#
# All functions take a DataFrame with open/high/low/close/ATR columns
# (ATR must already be computed -- see indicators.calc_atr) and an index
# position `i`, and return a bool for whether that specific candle at
# position i qualifies as the named pattern. Two/three-candle patterns
# look at df.iloc[i-1], df.iloc[i-2] etc. relative to i.
# ==============================================================================

import pandas as pd


def _body(row) -> float:
    return abs(row["close"] - row["open"])


def _upper_wick(row) -> float:
    return row["high"] - max(row["close"], row["open"])


def _lower_wick(row) -> float:
    return min(row["close"], row["open"]) - row["low"]


def _is_bullish(row) -> bool:
    return row["close"] > row["open"]


def _is_bearish(row) -> bool:
    return row["close"] < row["open"]


# ══════════════════════════════════════════════════════════════════════════
# Single-candle patterns
# ══════════════════════════════════════════════════════════════════════════

def is_doji(row, atr: float, body_atr_max: float = 0.1) -> bool:
    """Body is essentially zero relative to volatility -- pure indecision."""
    return _body(row) <= body_atr_max * atr


def is_hammer(row, atr: float, wick_body_ratio: float = 2.0, upper_wick_max_ratio: float = 0.3) -> bool:
    """Long lower wick (>=2x body), small/no upper wick. Meaning is
    context-dependent (bullish reversal at support, bearish 'hanging man'
    at resistance) -- the strategy layer decides that from where the
    candle sits, this function only confirms the shape."""
    body = _body(row)
    if body == 0:
        return False
    return (_lower_wick(row) >= wick_body_ratio * body) and (_upper_wick(row) <= upper_wick_max_ratio * body)


def is_inverted_hammer(row, atr: float, wick_body_ratio: float = 2.0, lower_wick_max_ratio: float = 0.3) -> bool:
    """Long upper wick (>=2x body), small/no lower wick. Same dual-meaning
    logic as is_hammer: bullish inverted hammer at support, bearish
    shooting star at resistance -- context decided by strategy layer."""
    body = _body(row)
    if body == 0:
        return False
    return (_upper_wick(row) >= wick_body_ratio * body) and (_lower_wick(row) <= lower_wick_max_ratio * body)


def is_marubozu(row, atr: float, body_atr_min: float = 1.5, wick_max_ratio: float = 0.05) -> bool:
    """The 'strong power candle' -- full-bodied, ~zero wicks, body is a
    large multiple of ATR. This is the highest-conviction single-candle
    signal in the taxonomy and should be weighted highest wherever
    confluence scoring combines multiple candle signals."""
    body = _body(row)
    if body < body_atr_min * atr:
        return False
    total_range = row["high"] - row["low"]
    if total_range == 0:
        return False
    wick_total = _upper_wick(row) + _lower_wick(row)
    return (wick_total / total_range) <= wick_max_ratio


def is_spinning_top(row, atr: float, body_atr_max: float = 0.3, wick_min_ratio: float = 1.0) -> bool:
    """Small body, meaningful wicks on both sides -- indecision/exhaustion,
    distinct from a doji by having real (if balanced) wicks rather than
    almost none."""
    body = _body(row)
    if body == 0 or body > body_atr_max * atr:
        return False
    return (_upper_wick(row) >= wick_min_ratio * body) and (_lower_wick(row) >= wick_min_ratio * body)


# ══════════════════════════════════════════════════════════════════════════
# Two-candle patterns (df, i -> current candle is df.iloc[i], prior is i-1)
# ══════════════════════════════════════════════════════════════════════════

def is_bullish_engulfing(df: pd.DataFrame, i: int) -> bool:
    if i < 1:
        return False
    prev, curr = df.iloc[i - 1], df.iloc[i]
    return (_is_bearish(prev) and _is_bullish(curr)
            and curr["close"] >= prev["open"] and curr["open"] <= prev["close"])


def is_bearish_engulfing(df: pd.DataFrame, i: int) -> bool:
    if i < 1:
        return False
    prev, curr = df.iloc[i - 1], df.iloc[i]
    return (_is_bullish(prev) and _is_bearish(curr)
            and curr["close"] <= prev["open"] and curr["open"] >= prev["close"])


def is_piercing_line(df: pd.DataFrame, i: int) -> bool:
    """Bullish: opens below prior close, closes above prior candle's midpoint
    (but does not fully engulf -- that would be an engulfing pattern instead)."""
    if i < 1:
        return False
    prev, curr = df.iloc[i - 1], df.iloc[i]
    if not (_is_bearish(prev) and _is_bullish(curr)):
        return False
    prev_mid = (prev["open"] + prev["close"]) / 2
    return curr["open"] < prev["close"] and prev_mid < curr["close"] < prev["open"]


def is_dark_cloud_cover(df: pd.DataFrame, i: int) -> bool:
    if i < 1:
        return False
    prev, curr = df.iloc[i - 1], df.iloc[i]
    if not (_is_bullish(prev) and _is_bearish(curr)):
        return False
    prev_mid = (prev["open"] + prev["close"]) / 2
    return curr["open"] > prev["close"] and prev["open"] < curr["close"] < prev_mid


def is_tweezer_top(df: pd.DataFrame, i: int, atr: float, tolerance_atr: float = 0.15) -> bool:
    if i < 1:
        return False
    prev, curr = df.iloc[i - 1], df.iloc[i]
    return abs(prev["high"] - curr["high"]) <= tolerance_atr * atr and _is_bullish(prev) and _is_bearish(curr)


def is_tweezer_bottom(df: pd.DataFrame, i: int, atr: float, tolerance_atr: float = 0.15) -> bool:
    if i < 1:
        return False
    prev, curr = df.iloc[i - 1], df.iloc[i]
    return abs(prev["low"] - curr["low"]) <= tolerance_atr * atr and _is_bearish(prev) and _is_bullish(curr)


# ══════════════════════════════════════════════════════════════════════════
# Three-candle patterns
# ══════════════════════════════════════════════════════════════════════════

def is_morning_star(df: pd.DataFrame, i: int, atr: float, mid_body_atr_max: float = 0.4) -> bool:
    if i < 2:
        return False
    c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    c1_mid = (c1["open"] + c1["close"]) / 2
    return (_is_bearish(c1) and _body(c1) > 0.5 * atr
            and _body(c2) <= mid_body_atr_max * atr
            and _is_bullish(c3) and c3["close"] > c1_mid)


def is_evening_star(df: pd.DataFrame, i: int, atr: float, mid_body_atr_max: float = 0.4) -> bool:
    if i < 2:
        return False
    c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    c1_mid = (c1["open"] + c1["close"]) / 2
    return (_is_bullish(c1) and _body(c1) > 0.5 * atr
            and _body(c2) <= mid_body_atr_max * atr
            and _is_bearish(c3) and c3["close"] < c1_mid)


def is_three_white_soldiers(df: pd.DataFrame, i: int, atr: float, body_atr_min: float = 0.5) -> bool:
    if i < 2:
        return False
    c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    candles = [c1, c2, c3]
    if not all(_is_bullish(c) and _body(c) >= body_atr_min * atr for c in candles):
        return False
    return c2["close"] > c1["close"] and c3["close"] > c2["close"] and \
           c2["open"] > c1["open"] and c3["open"] > c2["open"]


def is_three_black_crows(df: pd.DataFrame, i: int, atr: float, body_atr_min: float = 0.5) -> bool:
    if i < 2:
        return False
    c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    candles = [c1, c2, c3]
    if not all(_is_bearish(c) and _body(c) >= body_atr_min * atr for c in candles):
        return False
    return c2["close"] < c1["close"] and c3["close"] < c2["close"] and \
           c2["open"] < c1["open"] and c3["open"] < c2["open"]


def is_three_inside_up(df: pd.DataFrame, i: int) -> bool:
    """Bearish candle, then a smaller bullish 'harami' body fully inside it,
    then a third candle closing above the first candle's open (confirmation)."""
    if i < 2:
        return False
    c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    harami = (_is_bearish(c1) and _is_bullish(c2)
              and c2["open"] > c1["close"] and c2["close"] < c1["open"])
    return harami and _is_bullish(c3) and c3["close"] > c1["open"]


def is_three_inside_down(df: pd.DataFrame, i: int) -> bool:
    if i < 2:
        return False
    c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    harami = (_is_bullish(c1) and _is_bearish(c2)
              and c2["open"] < c1["close"] and c2["close"] > c1["open"])
    return harami and _is_bearish(c3) and c3["close"] < c1["open"]


# ══════════════════════════════════════════════════════════════════════════
# Convenience: scan a whole df, return a DataFrame of pattern flags
# ══════════════════════════════════════════════════════════════════════════

# Marubozu is weighted highest per the strategy spec -- this weight map is
# what confluence scoring in price_action/strategy.py reads, kept here so
# it lives next to the taxonomy it describes rather than duplicated.
PATTERN_WEIGHTS = {
    "marubozu": 3.0,
    "engulfing": 2.0,
    "morning_evening_star": 2.0,
    "three_soldiers_crows": 1.75,
    "hammer_inverted": 1.5,
    "piercing_darkcloud": 1.25,
    "tweezer": 1.0,
    "three_inside": 1.0,
    "doji_spinning_top": 0.5,
}


def scan_all_patterns(df: pd.DataFrame, atr_series: pd.Series) -> pd.DataFrame:
    """
    Runs every pattern function across the whole df. Returns a DataFrame
    (same index as df) with one boolean column per pattern. Intended for
    backtesting/research use (vectorized-ish convenience); live scanning
    in strategy.py should call individual pattern functions directly on
    the latest bar(s) rather than rescanning the whole history every scan.
    """
    n = len(df)
    cols = {
        "doji": [False] * n, "hammer": [False] * n, "inverted_hammer": [False] * n,
        "marubozu": [False] * n, "spinning_top": [False] * n,
        "bullish_engulfing": [False] * n, "bearish_engulfing": [False] * n,
        "piercing_line": [False] * n, "dark_cloud_cover": [False] * n,
        "tweezer_top": [False] * n, "tweezer_bottom": [False] * n,
        "morning_star": [False] * n, "evening_star": [False] * n,
        "three_white_soldiers": [False] * n, "three_black_crows": [False] * n,
        "three_inside_up": [False] * n, "three_inside_down": [False] * n,
    }

    for i in range(n):
        row = df.iloc[i]
        atr = atr_series.iloc[i]
        if pd.isna(atr) or atr == 0:
            continue

        cols["doji"][i] = is_doji(row, atr)
        cols["hammer"][i] = is_hammer(row, atr)
        cols["inverted_hammer"][i] = is_inverted_hammer(row, atr)
        cols["marubozu"][i] = is_marubozu(row, atr)
        cols["spinning_top"][i] = is_spinning_top(row, atr)

        cols["bullish_engulfing"][i] = is_bullish_engulfing(df, i)
        cols["bearish_engulfing"][i] = is_bearish_engulfing(df, i)
        cols["piercing_line"][i] = is_piercing_line(df, i)
        cols["dark_cloud_cover"][i] = is_dark_cloud_cover(df, i)
        cols["tweezer_top"][i] = is_tweezer_top(df, i, atr)
        cols["tweezer_bottom"][i] = is_tweezer_bottom(df, i, atr)

        cols["morning_star"][i] = is_morning_star(df, i, atr)
        cols["evening_star"][i] = is_evening_star(df, i, atr)
        cols["three_white_soldiers"][i] = is_three_white_soldiers(df, i, atr)
        cols["three_black_crows"][i] = is_three_black_crows(df, i, atr)
        cols["three_inside_up"][i] = is_three_inside_up(df, i)
        cols["three_inside_down"][i] = is_three_inside_down(df, i)

    return pd.DataFrame(cols, index=df.index)
