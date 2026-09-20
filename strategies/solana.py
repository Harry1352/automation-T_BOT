# ==============================================================================
# TESS — SOL/USDT Strategy Parameters
# ==============================================================================
# Walk-forward validated (Aug 23 2026):
#
#   1h  -- Early half: 71 trades, 24% WR, +$12.43
#          Late half:  59 trades, 27% WR, +$52.02
#          -> Confirmed on both halves, meaningfully improving over time.
#
#   15m -- Early half: 93 trades, 22% WR, +$13.69
#          Late half:  82 trades, 26% WR, +$26.74
#          -> Also confirmed, though 15m is confirmation-only in live Tess
#             (never a standalone decision basis), so this mainly matters
#             if that architecture changes later.
#
# Note: SOL/1h was also the single worst pair in the earlier single-window
# sweep before this fix (-$92 there). That number was misleading -- it came
# from testing the OLD, wrong reward:risk ratio (2.5 back then meant a
# harder breakeven bar). Under the correctly-matched RR here, SOL/1h is
# actually the SECOND-best confirmed edge in the whole sweep. Worth
# remembering as a lesson: a bad result can be a methodology bug, not a
# real signal about the coin -- always check before writing a pair off.
# ==============================================================================

PAIR = "SOL/USDT"
ADX_THRESHOLD = 20.0
REWARD_RISK_RATIO = 3.5
