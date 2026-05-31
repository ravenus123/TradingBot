"""Simple position sizing utilities.

Functions:
- compute_position_size(account_balance, stop_loss_pct, risk_pct=0.01)
- allocate_positions(account_balance, positions_info, risk_pct_each=0.01)
"""
from typing import Dict


def compute_position_size(account_balance: float, stop_loss_pct: float, risk_pct: float = 0.01) -> float:
    """Compute position size in currency units given account balance, stop-loss percent and risk percent.

    Example: account=100000, risk_pct=0.01 -> risk_amount=1000.
             stop_loss_pct=0.02 -> position_size = 1000 / 0.02 = 50000 (currency units).
    """
    if account_balance <= 0:
        raise ValueError("account_balance must be positive")
    if stop_loss_pct <= 0:
        raise ValueError("stop_loss_pct must be positive (fraction like 0.02 for 2%)")
    if not (0 < risk_pct < 1):
        raise ValueError("risk_pct must be between 0 and 1 (e.g. 0.01 for 1%)")

    risk_amount = account_balance * risk_pct
    position_size = risk_amount / stop_loss_pct
    return float(position_size)


def allocate_positions(account_balance: float, positions_info: Dict[str, float], risk_pct_each: float = 0.01) -> Dict[str, float]:
    """Allocate position sizes per symbol.

    positions_info: mapping symbol -> stop_loss_pct (fraction)
    Returns mapping symbol -> position_size (currency units).
    """
    sizes: Dict[str, float] = {}
    for sym, sl in positions_info.items():
        sizes[sym] = compute_position_size(account_balance, sl, risk_pct_each)
    return sizes
