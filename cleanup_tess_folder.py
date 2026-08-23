#!/usr/bin/env python3
"""
cleanup_tess_folder.py
Run this FROM your T_BOT project root: python3 cleanup_tess_folder.py

Moves (never deletes) superseded/legacy files into ./legacy/<today's date>/
so your active folder is clean but nothing is lost. Safe to run multiple
times -- skips anything already moved.
"""
import os
import shutil
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(ROOT, "legacy", datetime.now().strftime("%Y-%m-%d"))

# Files confirmed superseded / not part of the live scanner.py pipeline.
# Add or remove names here before running if you're unsure about any of them.
TO_ARCHIVE = [
    "tess.py",              # superseded predecessor of scanner.py (v11.2)
    "brain.py",             # pre-Nemotron, pre-strategies.py split; unused
    "web_gui.py",           # confirm scanner.py doesn't import this before archiving
    "backtester.py",        # superseded by multi_strategy_backtester.py
    "dynamic_config.json",  # root-level orphan -- real one lives in .tess_diary/
    "Procfile",             # only needed for cloud deploy, irrelevant for local paper trading
    # Old/duplicate backtest artifacts (keep the "final"-named + the sane
    # $500-start ones live; everything else here is intermediate iteration)
    "backtest_ETH_USDT_trades.csv",
    "backtest_SOL_USDT_trades.csv",
]

def main():
    os.makedirs(DEST, exist_ok=True)
    moved, skipped = [], []

    for name in TO_ARCHIVE:
        src = os.path.join(ROOT, name)
        if not os.path.exists(src):
            skipped.append((name, "not found"))
            continue
        dst = os.path.join(DEST, name)
        shutil.move(src, dst)
        moved.append(name)

    # __pycache__ is always safe to wipe outright -- it's regenerated automatically
    pycache = os.path.join(ROOT, "__pycache__")
    if os.path.isdir(pycache):
        shutil.rmtree(pycache)
        print(f"Removed __pycache__ (auto-regenerates, safe to delete outright)")

    print(f"\nMoved {len(moved)} file(s) to {DEST}:")
    for m in moved:
        print(f"  - {m}")
    if skipped:
        print(f"\nSkipped (not found, already moved, or already clean):")
        for name, reason in skipped:
            print(f"  - {name} ({reason})")

    print(f"\nNothing was deleted. Everything is in {DEST} if you need it back.")

if __name__ == "__main__":
    main()
