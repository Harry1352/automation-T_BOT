#!/bin/bash
# ==============================================================================
# TESS — Supervisor / Auto-Restart Wrapper (run_tess.sh)
# ==============================================================================
# Run THIS instead of "python3 scanner.py" directly. It:
#   1. Enforces sleep prevention properly (all 4 caffeinate flags, not just
#      whatever was being typed by hand before -- easy to drop one and not
#      notice).
#   2. If scanner.py crashes or exits for any reason, restarts it automatically
#      after a short pause, instead of silently leaving Tess dead until you
#      happen to check the terminal.
#   3. Logs every start/restart with a timestamp and reason into
#      .tess_diary/supervisor.log, separate from the trading log, so you can
#      tell "the market was quiet" apart from "the process actually died"
#      just by checking this file.
#
# Usage:
#   chmod +x run_tess.sh
#   ./run_tess.sh
#
# Recommended: run this INSIDE tmux so closing your terminal window doesn't
# kill it:
#   tmux new -s tess
#   ./run_tess.sh
#   (Ctrl+B then D to detach -- reattach any time with: tmux attach -t tess)
# ==============================================================================

cd "$(dirname "$0")"

SUPERVISOR_LOG=".tess_diary/supervisor.log"
mkdir -p .tess_diary

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$SUPERVISOR_LOG"
}

log "=== Supervisor started (PID $$) ==="

RESTART_COUNT=0

while true; do
    log "Launching scanner.py (restart #$RESTART_COUNT)..."

    # -d: prevent display sleep, -i: prevent idle sleep, -m: prevent disk
    # sleep, -s: prevent system sleep (AC power), -u: prevent user-idle
    # sleep (this is the one most often missing by hand -- it's what stops
    # macOS from sleeping just because you haven't touched the keyboard).
    caffeinate -dimsu python3 scanner.py
    EXIT_CODE=$?

    RESTART_COUNT=$((RESTART_COUNT + 1))
    log "scanner.py exited with code $EXIT_CODE. Restarting in 10 seconds... (total restarts: $RESTART_COUNT)"

    if [ "$RESTART_COUNT" -ge 10 ]; then
        log "WARNING: 10+ restarts in this session. Something is likely crash-looping "
        log "(bad API key, syntax error, network issue) rather than recovering normally. "
        log "Check the last lines of .tess_diary/tess_thinking.log before this keeps going."
    fi

    sleep 10
done
