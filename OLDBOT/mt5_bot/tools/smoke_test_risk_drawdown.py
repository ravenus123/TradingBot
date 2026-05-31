"""Smoke test for risk sizing and drawdown modules.

Run this file to verify basic behavior and output examples.
"""
import sys
import os

# Ensure repository root is on sys.path so OLDBOT can be imported when running the script directly
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from OLDBOT.mt5_bot import risk, drawdown


def main():
    print("Smoke test: risk.compute_position_size")
    pos = risk.compute_position_size(100000.0, 0.02, 0.01)
    print("  position_size:", pos)

    print("Smoke test: drawdown.max_drawdown")
    seq = [100000.0, 105000.0, 90000.0, 110000.0, 95000.0]
    md = drawdown.max_drawdown(seq)
    print("  max_drawdown_pct:", md)

    print("Smoke test: DrawdownMonitor")
    m = drawdown.DrawdownMonitor(max_dd_pct_threshold=10.0)
    for v in seq:
        exceeded, dd = m.update(v)
        print(f"  equity={v} dd={dd:.2f}% exceeded={exceeded}")


if __name__ == '__main__':
    main()
