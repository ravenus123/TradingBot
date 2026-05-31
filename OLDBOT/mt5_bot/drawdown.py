"""Drawdown utilities: compute max drawdown and a simple monitor for live checks."""
from typing import Iterable, Tuple

def max_drawdown(equity: Iterable[float]) -> float:
    """Return max drawdown as a positive percentage (e.g. 12.5 for 12.5%%).

    equity: iterable of equity values (chronological)
    """
    vals = [v for v in equity if v is not None]
    if not vals:
        return 0.0
    peak = vals[0]
    max_dd = 0.0
    for v in vals:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
    return float(max_dd * 100.0)


class DrawdownMonitor:
    """Simple online drawdown monitor.

    Usage:
        m = DrawdownMonitor(max_dd_pct_threshold=25.0)
        exceeded, dd = m.update(current_equity)
    """

    def __init__(self, max_dd_pct_threshold: float = 25.0):
        if max_dd_pct_threshold <= 0:
            raise ValueError("max_dd_pct_threshold must be > 0")
        self.max_dd_pct_threshold = max_dd_pct_threshold
        self.peak = None

    def update(self, current_equity: float) -> Tuple[bool, float]:
        """Update with current equity and return (exceeded_flag, current_dd_pct).

        current_dd_pct is positive when underwater.
        """
        if self.peak is None or current_equity > self.peak:
            self.peak = current_equity
        dd = (self.peak - current_equity) / self.peak * 100.0
        exceeded = dd >= self.max_dd_pct_threshold
        return exceeded, float(dd)
