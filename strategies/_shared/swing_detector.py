# ==============================================================================
# TESS — _shared/swing_detector.py (v1.0)
# ==============================================================================
# N-bar fractal pivot detection. This is the single source of truth for
# "what is a swing high/low" across price_action, fibonacci, and
# price_action_smc — all three need this exact same concept (a local
# extreme confirmed by N bars on either side) and must agree on it, or
# their zones/levels/structure calls would silently disagree with each
# other for no real reason.
#
# `n` (bars required on each side to confirm a pivot) is a swept parameter
# in the backtest harness, not a fixed guess — different coins/timeframes
# may genuinely need a tighter or looser fractal window.
# ==============================================================================

import numpy as np
import pandas as pd


def find_swing_points(df: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """
    A bar at index i is a confirmed swing HIGH if its high is the max of
    the window [i-n, i+n], and a confirmed swing LOW if its low is the min
    of that window. This means the most recent `n` bars can never be
    confirmed yet (not enough bars on the right side) -- that's correct
    and intentional: a swing point isn't real until price has moved away
    from it. Strategies must never use swing points that couldn't have
    been known at signal time; this function returns confirmation status
    at the bar where it becomes knowable, not before.

    Returns the input df with two added boolean columns:
      - swing_high: True where this bar is a confirmed fractal high
      - swing_low:  True where this bar is a confirmed fractal low
    """
    df = df.copy()
    high, low = df["high"].values, df["low"].values
    length = len(df)

    swing_high = np.zeros(length, dtype=bool)
    swing_low = np.zeros(length, dtype=bool)

    for i in range(n, length - n):
        window_high = high[i - n: i + n + 1]
        window_low = low[i - n: i + n + 1]
        if high[i] == window_high.max() and (window_high == high[i]).sum() == 1:
            swing_high[i] = True
        if low[i] == window_low.min() and (window_low == low[i]).sum() == 1:
            swing_low[i] = True

    df["swing_high"] = swing_high
    df["swing_low"] = swing_low
    return df


def get_last_confirmed_swing(df: pd.DataFrame, up_to_index: int, kind: str):
    """
    Walks backward from up_to_index (exclusive) to find the most recent
    confirmed swing_high or swing_low. kind must be "high" or "low".
    Returns (index_position, price) or (None, None) if no swing exists yet
    in the available history. Used by fibonacci/ (swing high->low anchor)
    and price_action_smc/ (last swing for BOS/CHoCH comparison).
    """
    col = "swing_high" if kind == "high" else "swing_low"
    price_col = "high" if kind == "high" else "low"
    for i in range(up_to_index - 1, -1, -1):
        if df[col].iloc[i]:
            return i, df[price_col].iloc[i]
    return None, None


def get_swing_sequence(df: pd.DataFrame, kind: str, count: int = 3, up_to_index: int = None):
    """
    Returns the last `count` confirmed swings of the given kind (as a list
    of (index_position, price) tuples, oldest first), up to up_to_index.
    Used for pattern geometry checks that need more than one prior swing --
    e.g. Head & Shoulders needs 3 highs, Double Top needs 2, triangle
    trendlines need several points to regress through.
    """
    if up_to_index is None:
        up_to_index = len(df)
    col = "swing_high" if kind == "high" else "swing_low"
    price_col = "high" if kind == "high" else "low"

    found = []
    for i in range(up_to_index - 1, -1, -1):
        if df[col].iloc[i]:
            found.append((i, df[price_col].iloc[i]))
            if len(found) == count:
                break
    return list(reversed(found))


def swings_within_tolerance(price_a: float, price_b: float, tolerance_pct: float) -> bool:
    """
    Shared 'are these two swing prices close enough to count as the same
    level' check -- used by Double Top/Bottom, Triple Top/Bottom, Tweezer
    patterns, and S/R zone clustering. tolerance_pct is swept in the
    backtest harness (a tight tolerance misses valid patterns with normal
    noise; a loose one falsely matches unrelated swings).
    """
    if price_a == 0:
        return False
    return abs(price_a - price_b) / price_a <= tolerance_pct
