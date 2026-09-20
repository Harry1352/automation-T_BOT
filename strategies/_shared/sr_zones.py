# ==============================================================================
# TESS — _shared/sr_zones.py (v1.0)
# ==============================================================================
# Clusters swing points (from swing_detector.py) into support/resistance
# price zones. Used by price_action/ directly (zone touches, confluence
# scoring) and by fibonacci/ (optional confluence layer). A "zone" here is
# a price band, not a single line -- markets react to a range, not an exact
# tick, and treating S/R as a single price is what makes eyeballed S/R
# unreliable in the first place.
# ==============================================================================

import pandas as pd
from .swing_detector import swings_within_tolerance


def build_sr_zones(df: pd.DataFrame, zone_tolerance_pct: float = 0.0025,
                    min_touches: int = 2, recency_halflife_bars: int = 200) -> list:
    """
    Clusters all confirmed swing_high / swing_low points (df must already
    have those columns -- run swing_detector.find_swing_points first) into
    zones. Two swings merge into the same zone if their prices are within
    zone_tolerance_pct of the zone's running average price.

    Each zone gets a score = touch_count * recency_weight, where
    recency_weight decays exponentially the longer it's been since the
    zone was last touched (recency_halflife_bars controls how fast old
    zones fade -- an S/R level nobody has touched in 500 bars matters less
    than one touched 10 bars ago, even with the same touch count).

    zone_tolerance_pct and min_touches are both swept in the backtest
    harness per coin/timeframe -- a zone definition tight enough for BTC
    may be too tight for a more volatile pair.

    Returns a list of dicts, sorted by score descending:
      {"price": float (zone center), "kind": "resistance"|"support"|"both",
       "touches": int, "touch_indices": [...], "score": float,
       "last_touch_index": int}
    """
    swing_rows = []
    if "swing_high" in df.columns:
        for i in df.index[df["swing_high"]].tolist():
            pos = df.index.get_loc(i)
            swing_rows.append({"pos": pos, "price": df.loc[i, "high"], "kind": "resistance"})
    if "swing_low" in df.columns:
        for i in df.index[df["swing_low"]].tolist():
            pos = df.index.get_loc(i)
            swing_rows.append({"pos": pos, "price": df.loc[i, "low"], "kind": "support"})

    swing_rows.sort(key=lambda r: r["pos"])

    zones = []
    for row in swing_rows:
        merged = False
        for zone in zones:
            if swings_within_tolerance(zone["price"], row["price"], zone_tolerance_pct):
                # merge: update running average price, add touch, update kind
                n = zone["touches"]
                zone["price"] = (zone["price"] * n + row["price"]) / (n + 1)
                zone["touches"] += 1
                zone["touch_indices"].append(row["pos"])
                zone["last_touch_index"] = row["pos"]
                if zone["kind"] != row["kind"]:
                    zone["kind"] = "both"  # price acted as both support and resistance over time
                merged = True
                break
        if not merged:
            zones.append({
                "price": row["price"],
                "kind": row["kind"],
                "touches": 1,
                "touch_indices": [row["pos"]],
                "last_touch_index": row["pos"],
            })

    last_bar = len(df) - 1
    scored_zones = []
    for zone in zones:
        if zone["touches"] < min_touches:
            continue
        bars_since_touch = last_bar - zone["last_touch_index"]
        recency_weight = 0.5 ** (bars_since_touch / recency_halflife_bars)
        zone["score"] = zone["touches"] * recency_weight
        scored_zones.append(zone)

    scored_zones.sort(key=lambda z: z["score"], reverse=True)
    return scored_zones


def nearest_zone(zones: list, price: float, direction: str = "any"):
    """
    Returns the nearest zone to `price`. direction="above" only considers
    zones priced above `price` (used to find the next resistance/TP target
    for a long), direction="below" only considers zones below (TP target
    for a short), "any" considers both.
    """
    candidates = zones
    if direction == "above":
        candidates = [z for z in zones if z["price"] > price]
    elif direction == "below":
        candidates = [z for z in zones if z["price"] < price]

    if not candidates:
        return None
    return min(candidates, key=lambda z: abs(z["price"] - price))


def is_ema200_confluence(zone_price: float, ema200_value: float, tolerance_pct: float = 0.005) -> bool:
    """
    Booming Bulls' confluence rule: a horizontal S/R zone overlapping the
    200 EMA is scored as a higher-probability zone. This returns a plain
    bool; the strategy layer decides how much weight to give it (see
    price_action/strategy.py confluence scoring) rather than baking a
    fixed weight in here.
    """
    if ema200_value == 0:
        return False
    return abs(zone_price - ema200_value) / ema200_value <= tolerance_pct
