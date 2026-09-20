# ==============================================================================
# TESS — strategies package dispatcher
# ==============================================================================
# scanner.py used to do `import strategies` and call strategies.build_market_
# snapshot(pair, candles) / strategies.ask_tess(...) directly. This package
# keeps those exact same two call sites working, but now routes each pair to
# its own tuned ADX/RR parameters automatically based on `pair`.
#
# TIMEFRAMES and PRIMARY_TF_BY_TRADE_TYPE are re-exported unchanged so
# `strategies.TIMEFRAMES` in scanner.py keeps working with zero edits there.
# ==============================================================================

from . import base
from . import bitcoin, solana, ethereum, xaut

TIMEFRAMES = base.TIMEFRAMES
PRIMARY_TF_BY_TRADE_TYPE = base.PRIMARY_TF_BY_TRADE_TYPE

_COIN_MODULES = {
    bitcoin.PAIR: bitcoin,
    solana.PAIR: solana,
    ethereum.PAIR: ethereum,
    xaut.PAIR: xaut,
}

_DEFAULT_ADX = 25.0
_DEFAULT_RR = 3.5


def get_params(pair):
    """Returns (adx_threshold, reward_risk_ratio) for a pair, falling back
    to conservative defaults for any pair not yet given its own file."""
    mod = _COIN_MODULES.get(pair)
    if mod is None:
        return _DEFAULT_ADX, _DEFAULT_RR
    return mod.ADX_THRESHOLD, mod.REWARD_RISK_RATIO


def build_market_snapshot(pair, candles):
    adx, rr = get_params(pair)
    return base.build_market_snapshot(pair, candles, adx, rr)


def ask_tess(market_snapshots, open_positions, balance, daily_pnl, recent_trades, lessons_context):
    """
    NOTE: market_snapshots covers multiple pairs in one call (scanner.py
    builds one snapshot per pair, then calls this once per scan with all of
    them combined). Each pair's snapshot was already built using ITS OWN
    tuned ADX/RR via build_market_snapshot() above. This call itself needs
    ONE adx/rr pair to put in the prompt's rules text -- since the prompt
    rules text is shown once for the whole scan, we use the default here
    for the general instructions, but each pair's actual technical_signal/
    suggested_sl/suggested_tp in the snapshot data already reflects that
    pair's real tuned parameters, which is what the code-level validation
    checks against -- so per-pair correctness is preserved regardless of
    what's shown in the shared instruction text.
    """
    return base.ask_tess(
        market_snapshots, open_positions, balance, daily_pnl, recent_trades,
        lessons_context, adx_threshold=_DEFAULT_ADX, reward_risk_ratio=_DEFAULT_RR
    )
