# ==============================================================================
# TESS — XAUT/USDT Strategy Parameters
# ==============================================================================
# Walk-forward validated (Aug 23 2026):
#
#   1h  -- NO combo ranked top-5 on both halves. Nothing survived at all.
#   15m -- One combo (ADX=30, RR=3.0) ranked on both halves but was barely
#          profitable in one (+$0.86) and lost in the other (-$1.33) --
#          not a real edge, just noise close to zero.
#
# HONEST RECOMMENDATION: this pair has shown NO confirmed, repeatable edge
# under this strategy across either timeframe, in two independent backtest
# windows. Keeping the conservative default here rather than inventing a
# number, but seriously consider removing XAUT/USDT from TRADING_PAIRS in
# config.py until either a different strategy is tested for it specifically,
# or more live data suggests otherwise. Trading a pair with no evidence of
# an edge, purely because it's on the list, is exactly the kind of thing
# this whole rigorous-backtesting effort was meant to avoid.
# ==============================================================================

PAIR = "XAUT/USDT"
ADX_THRESHOLD = 25.0
REWARD_RISK_RATIO = 3.5
