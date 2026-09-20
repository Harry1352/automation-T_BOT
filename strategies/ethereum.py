# ==============================================================================
# TESS — ETH/USDT Strategy Parameters
# ==============================================================================
# Walk-forward validated (Aug 23 2026):
#
#   1h  -- NO CONFIRMED EDGE. The two combos that ranked well on one half
#          (ADX=14/RR=2.5 and ADX=14/RR=3.5) both LOST money on at least one
#          half (-$47.05 and -$7.15/-$14.66 respectively). They only showed
#          up in the raw sweep because ETH/1h was weak across the board, not
#          because these settings actually work. Keeping the conservative
#          default (ADX=25, RR=3.5) here until real evidence says otherwise.
#
#   15m -- Confirmed on both halves: ADX=30, RR=2.5 or 3.5
#          Early: 29-33 trades, 28-36% WR, +$20 to +$26
#          Late:  39-45 trades, 23-36% WR, +$18 to +$23
#          -> Real, but 15m is confirmation-only in live Tess right now, so
#             this doesn't change swing/position execution yet.
#
# Bottom line: don't trust ETH swing trades any more than the default
# setting already does. If you want a real ETH edge, it may need a
# different rule entirely, not just different ADX/RR numbers -- the sweep
# tried 16 combos and none of them held up on 1h.
# ==============================================================================

PAIR = "ETH/USDT"
ADX_THRESHOLD = 25.0
REWARD_RISK_RATIO = 3.5
