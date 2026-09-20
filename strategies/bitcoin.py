# ==============================================================================
# TESS — BTC/USDT Strategy Parameters
# ==============================================================================
# Walk-forward validated (Aug 23 2026): tested on TWO independent, non-
# overlapping time halves. A combo only lives here if it was profitable on
# BOTH halves independently, not just the combined window.
#
#   1h  -- Early half (2024-05-12 to 2025-07-02): 121 trades, 36% WR, +$27.37
#          Late half  (2025-07-02 to 2026-08-23): 115 trades, 37% WR, +$64.79
#          -> Strongest, most consistent edge found across the entire sweep.
#             Edge IMPROVED over time, not decayed -- good sign.
#
# No confirmed edge found for BTC on 15m specifically (nothing beat the
# ADX=25/RR=3.5 default on both halves there), so 15m confirmation-only
# usage is unaffected -- this file's parameters matter most for "swing"
# (1h primary) trades.
# ==============================================================================

PAIR = "BTC/USDT"
ADX_THRESHOLD = 14.0
REWARD_RISK_RATIO = 2.5
