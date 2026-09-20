# ==============================================================================
# TESS — _shared/indicators.py (v1.0)
# ==============================================================================
# Common indicator math used by ALL FOUR strategy families
# (technical_analysis, price_action, fibonacci, price_action_smc).
#
# The EMA/RSI/ATR/ADX implementations here are copied verbatim from the
# existing strategies/base.py (the live 9/20/200 system) rather than
# reimplemented, so every family's "trend" and "volatility" reads are
# numerically identical to what Tess already trades on. New indicators
# added for this rebuild (MACD, Bollinger, VWAP, Stoch RSI) follow the
# same style: pure functions, no side effects, no network calls, no file
# writes — input a DataFrame with high/low/close/volume, get a Series or
# DataFrame of computed values back.
#
# df is always expected to have: "open", "high", "low", "close", "volume"
# columns, indexed in ascending time order.
# ==============================================================================

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════════════════
# Existing indicators — unchanged from base.py, reused so all families agree
# ══════════════════════════════════════════════════════════════════════════

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    try:
        high, low, close = df["high"], df["low"], df["close"]
        plus_dm = high.diff()
        minus_dm = low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        hl = high - low
        hc = (high - close.shift()).abs()
        lc = (low - close.shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        return dx.rolling(period).mean()
    except Exception:
        return pd.Series(0, index=df.index)


# ══════════════════════════════════════════════════════════════════════════
# New for the strategy rebuild
# ══════════════════════════════════════════════════════════════════════════

def calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Returns a DataFrame with columns: macd_line, signal_line, histogram."""
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return pd.DataFrame({
        "macd_line": macd_line,
        "signal_line": signal_line,
        "histogram": histogram,
    })


def calc_bollinger_bands(close: pd.Series, period: int = 20, std_mult: float = 2.0) -> pd.DataFrame:
    """Returns columns: bb_mid, bb_upper, bb_lower, bb_width, bb_width_pct.
    bb_width_pct is width normalized by the mid band — the actual number
    used for squeeze detection (low percentile = compression = potential
    breakout regime), since raw width isn't comparable across price levels
    or across coins."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width = upper - lower
    width_pct = width / mid
    return pd.DataFrame({
        "bb_mid": mid, "bb_upper": upper, "bb_lower": lower,
        "bb_width": width, "bb_width_pct": width_pct,
    })


def is_bb_squeeze(bb_width_pct: pd.Series, lookback: int = 100, percentile: float = 20.0) -> pd.Series:
    """True where current width_pct sits in the bottom `percentile` of the
    trailing `lookback` window — i.e. genuinely compressed relative to this
    coin's own recent volatility, not an arbitrary fixed number that means
    something different on ETH than on XAUT."""
    rolling_pct_rank = bb_width_pct.rolling(lookback).apply(
        lambda x: (x[-1] <= np.percentile(x, percentile)) if len(x) == lookback else np.nan,
        raw=True,
    )
    return rolling_pct_rank.astype(bool).fillna(False)


def calc_vwap(df: pd.DataFrame, reset_daily: bool = True) -> pd.Series:
    """Volume-weighted average price. If reset_daily, resets the cumulative
    sums at each new UTC day (standard VWAP behavior — otherwise it's an
    all-time average, which isn't useful as an intraday bias line).
    df.index must be a DatetimeIndex for reset_daily to work; falls back to
    a single running VWAP over the whole df if the index isn't datetime."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical_price * df["volume"]

    if reset_daily and isinstance(df.index, pd.DatetimeIndex):
        day = df.index.date
        cum_pv = pv.groupby(day).cumsum()
        cum_vol = df["volume"].groupby(day).cumsum()
    else:
        cum_pv = pv.cumsum()
        cum_vol = df["volume"].cumsum()

    return cum_pv / cum_vol


def calc_stoch_rsi(close: pd.Series, rsi_period: int = 14, stoch_period: int = 14,
                    smooth_k: int = 3, smooth_d: int = 3) -> pd.DataFrame:
    """Stochastic RSI: applies the Stochastic oscillator formula to RSI
    values instead of price. Returns columns: stoch_rsi_k, stoch_rsi_d
    (both 0-100 scale, matching the standard >80 overbought / <20 oversold
    convention Booming Bulls uses as the entry timing trigger)."""
    rsi = calc_rsi(close, rsi_period)
    rsi_min = rsi.rolling(stoch_period).min()
    rsi_max = rsi.rolling(stoch_period).max()
    stoch_rsi_raw = 100 * (rsi - rsi_min) / (rsi_max - rsi_min)
    k = stoch_rsi_raw.rolling(smooth_k).mean()
    d = k.rolling(smooth_d).mean()
    return pd.DataFrame({"stoch_rsi_k": k, "stoch_rsi_d": d})


def calc_volume_confirmation(volume: pd.Series, period: int = 20) -> pd.DataFrame:
    """Returns columns: vol_ma, vol_ratio (current / rolling average),
    vol_confirmed (bool — current bar's volume at or above its own average).
    Used identically across all 4 families as the volume-confirmation gate."""
    vol_ma = volume.rolling(period).mean()
    vol_ratio = volume / vol_ma
    return pd.DataFrame({
        "vol_ma": vol_ma,
        "vol_ratio": vol_ratio,
        "vol_confirmed": (volume >= vol_ma),
    })


def detect_rsi_divergence(close: pd.Series, rsi: pd.Series, swing_lookback: int = 5) -> pd.DataFrame:
    """Regular divergence detection (the actual signal, not just OB/OS
    threshold crossing): price makes a new local extreme that RSI does NOT
    confirm. Returns columns: bullish_divergence, bearish_divergence (bool).

    Bullish divergence: price makes a lower low, RSI makes a higher low
    (selling momentum is fading even as price still falls -- a genuine
    early-reversal signal).
    Bearish divergence: price makes a higher high, RSI makes a lower high.

    swing_lookback defines how many bars back the prior local extreme is
    compared against -- this is a swept parameter in the backtest harness,
    not a fixed guess."""
    price_low = close.rolling(swing_lookback, center=True).min() == close
    price_high = close.rolling(swing_lookback, center=True).max() == close

    prev_swing_low_price = close.where(price_low).ffill().shift(1)
    prev_swing_low_rsi = rsi.where(price_low).ffill().shift(1)
    prev_swing_high_price = close.where(price_high).ffill().shift(1)
    prev_swing_high_rsi = rsi.where(price_high).ffill().shift(1)

    bullish_div = (
        price_low
        & (close < prev_swing_low_price)
        & (rsi > prev_swing_low_rsi)
    )
    bearish_div = (
        price_high
        & (close > prev_swing_high_price)
        & (rsi < prev_swing_high_rsi)
    )
    return pd.DataFrame({
        "bullish_divergence": bullish_div.fillna(False),
        "bearish_divergence": bearish_div.fillna(False),
    })
