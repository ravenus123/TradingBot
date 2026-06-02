from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd


SignalGenerator = Callable[[str, dict], Optional[dict]]


@dataclass
class StrategySpec:
    name: str
    style: str
    asset_class: str
    weight: float
    enabled: bool
    generator: SignalGenerator


class StrategyRegistry:
    """Registry for modular strategy inputs.

    A strategy is any callable that returns a normalized signal dict or None.
    """

    def __init__(self) -> None:
        self._strategies: Dict[str, StrategySpec] = {}

    def register(self, spec: StrategySpec) -> None:
        self._strategies[spec.name] = spec

    def active(self) -> List[StrategySpec]:
        return [spec for spec in self._strategies.values() if spec.enabled]


class PortfolioRiskManager:
    """Portfolio-level risk controls to survive variance across strategies."""

    def __init__(self, max_risk_per_trade_pct: float = 1.0, max_open_trades: int = 10):
        self.max_risk_per_trade_pct = float(max_risk_per_trade_pct)
        self.max_open_trades = int(max_open_trades)

    def clamp_risk(self, requested_risk_pct: float, open_positions: int) -> float:
        if open_positions >= self.max_open_trades:
            return 0.0
        return max(0.0, min(float(requested_risk_pct), self.max_risk_per_trade_pct))


class PortfolioOrchestrator:
    """Run multiple strategies and choose one candidate signal for execution."""

    def __init__(self, registry: StrategyRegistry, risk_manager: PortfolioRiskManager):
        self.registry = registry
        self.risk_manager = risk_manager

    def generate_signal(self, symbol: str, context: dict) -> Optional[dict]:
        """Generate signal for portfolio orchestrator.
        
        For multi-strategy portfolio, this returns the best signal from all strategies.
        The scan loop will call this once per symbol.
        """
        candidates: List[dict] = []
        for spec in self.registry.active():
            signal = spec.generator(symbol, context)
            if not signal:
                continue
            signal['strategy_name'] = spec.name
            signal['strategy_style'] = spec.style
            signal['strategy_weight'] = float(spec.weight)
            score = float(signal.get('confluence_score', 0.0)) + float(spec.weight)
            signal['_portfolio_rank'] = score
            candidates.append(signal)

        if not candidates:
            return None

        candidates.sort(key=lambda s: s['_portfolio_rank'], reverse=True)
        best = candidates[0]
        best.pop('_portfolio_rank', None)
        return best

    def generate_all_signals(self, symbol: str, context: dict) -> List[dict]:
        """Generate ALL signals from all strategies (for multi-strategy portfolio).
        
        Returns a list of all valid signals from all strategies.
        Each strategy can generate its own signal independently.
        """
        signals: List[dict] = []
        for spec in self.registry.active():
            signal = spec.generator(symbol, context)
            if not signal:
                continue
            signal['strategy_name'] = spec.name
            signal['strategy_style'] = spec.style
            signal['strategy_weight'] = float(spec.weight)
            score = float(signal.get('confluence_score', 0.0)) + float(spec.weight)
            signal['_portfolio_rank'] = score
            signals.append(signal)

        return signals


def build_trade_correlation_matrix(trades: pd.DataFrame) -> pd.DataFrame:
    """Build strategy return correlation matrix from closed trade logs.

    Expected columns: strategy_name, close_time, profit.
    Returns an empty DataFrame if not enough data.
    """
    if trades is None or trades.empty:
        return pd.DataFrame()
    required = {'strategy_name', 'close_time', 'profit'}
    if not required.issubset(set(trades.columns)):
        return pd.DataFrame()

    df = trades.copy()
    df['close_time'] = pd.to_datetime(df['close_time'], errors='coerce')
    df = df.dropna(subset=['close_time'])
    if df.empty:
        return pd.DataFrame()

    df['trade_day'] = df['close_time'].dt.floor('D')
    daily = (
        df.groupby(['trade_day', 'strategy_name'], as_index=False)['profit']
        .sum()
    )
    pivot = daily.pivot(index='trade_day', columns='strategy_name', values='profit').fillna(0.0)
    if pivot.shape[0] < 5 or pivot.shape[1] < 2:
        return pd.DataFrame()
    corr = pivot.corr()
    corr = corr.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return corr
