"""
Comprehensive data collection for hedge fund research.
Collects all data needed to validate edge and compare with backtests.
"""
import json
import csv
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional
import pandas as pd
import numpy as np


class HedgeFundDataCollector:
    """Collects comprehensive data for hedge fund research and edge validation."""
    
    def __init__(self, base_dir: Path = None):
        if base_dir is None:
            base_dir = Path(__file__).parent / 'hedgefunddata'
        
        self.base_dir = Path(base_dir)
        self.trades_dir = self.base_dir / 'trades'
        self.performance_dir = self.base_dir / 'performance'
        self.regime_dir = self.base_dir / 'regime'
        self.infrastructure_dir = self.base_dir / 'infrastructure'
        
        # Create directories
        for dir_path in [self.trades_dir, self.performance_dir, self.regime_dir, self.infrastructure_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Session start time
        self.session_start = datetime.now(timezone.utc)
        
        # Performance tracking
        self.equity_curve = []
        self.drawdown_curve = []
        self.daily_pnl = []
        
        # Strategy performance tracking
        self.strategy_performance = {}  # {strategy_label: {trades, win_rate, avg_return, ...}}
        
        # Infrastructure events
        self.infrastructure_events = []
        
        # Market regime data
        self.regime_data = []
        
        # Trade execution data
        self.trade_execution_log = []
    
    def log_trade_execution(self, trade_data: Dict):
        """
        Log comprehensive trade execution data.
        
        trade_data should include:
        - timestamp: datetime
        - symbol: str
        - strategy: str
        - label: str
        - direction: str (BUY/SELL)
        - entry_price: float
        - stop_loss: float
        - take_profit: float
        - lot_size: float
        - risk_percent: float
        - atr: float (volatility at entry)
        - signal_score: int (if available)
        - signal_type: str (if available)
        - execution_time_ms: float (time to execute)
        - slippage_pips: float (if available)
        - spread_at_entry: float
        - status: str (OPEN/CLOSED)
        - exit_time: datetime (if closed)
        - exit_price: float (if closed)
        - profit: float (if closed)
        - holding_period_minutes: float (if closed)
        """
        trade_data['logged_at'] = datetime.now(timezone.utc).isoformat()
        self.trade_execution_log.append(trade_data)
        
        # Update strategy performance
        label = trade_data.get('label', 'unknown')
        if label not in self.strategy_performance:
            self.strategy_performance[label] = {
                'symbol': trade_data.get('symbol', ''),
                'strategy': trade_data.get('strategy', ''),
                'trades': 0,
                'wins': 0,
                'losses': 0,
                'total_profit': 0.0,
                'total_loss': 0.0,
                'avg_return': 0.0,
                'max_profit': 0.0,
                'max_loss': 0.0,
                'avg_holding_period': 0.0,
                'total_holding_period': 0.0,
            }
        
        perf = self.strategy_performance[label]
        perf['trades'] += 1
        
        if trade_data.get('status') == 'CLOSED':
            profit = trade_data.get('profit', 0)
            if profit > 0:
                perf['wins'] += 1
                perf['total_profit'] += profit
                perf['max_profit'] = max(perf['max_profit'], profit)
            else:
                perf['losses'] += 1
                perf['total_loss'] += abs(profit)
                perf['max_loss'] = max(perf['max_loss'], abs(profit))
            
            perf['avg_return'] = (perf['total_profit'] - perf['total_loss']) / perf['trades']
            
            holding_period = trade_data.get('holding_period_minutes', 0)
            perf['total_holding_period'] += holding_period
            perf['avg_holding_period'] = perf['total_holding_period'] / perf['trades']
        
        # Write to CSV
        self._write_trade_to_csv(trade_data)
    
    def _write_trade_to_csv(self, trade_data: Dict):
        """Write trade data to CSV file."""
        csv_file = self.trades_dir / f'trades_{self.session_start.strftime("%Y%m%d")}.csv'
        file_exists = csv_file.exists()
        
        fieldnames = [
            'logged_at', 'timestamp', 'symbol', 'strategy', 'label',
            'direction', 'entry_price', 'stop_loss', 'take_profit',
            'lot_size', 'risk_percent', 'atr', 'signal_score', 'signal_type',
            'execution_time_ms', 'slippage_pips', 'spread_at_entry',
            'status', 'exit_time', 'exit_price', 'profit', 'holding_period_minutes'
        ]
        
        with open(csv_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow({k: trade_data.get(k, '') for k in fieldnames})
    
    def log_performance_snapshot(self, equity: float, balance: float, open_pnl: float):
        """
        Log performance snapshot for equity curve and drawdown tracking.
        """
        timestamp = datetime.now(timezone.utc)
        
        # Calculate drawdown
        if not self.equity_curve:
            peak_equity = equity
            drawdown = 0.0
        else:
            peak_equity = max([e['equity'] for e in self.equity_curve] + [equity])
            drawdown = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0.0
        
        snapshot = {
            'timestamp': timestamp.isoformat(),
            'equity': equity,
            'balance': balance,
            'open_pnl': open_pnl,
            'peak_equity': peak_equity,
            'drawdown_pct': drawdown,
            'session_duration_hours': (timestamp - self.session_start).total_seconds() / 3600,
        }
        
        self.equity_curve.append(snapshot)
        self.drawdown_curve.append(drawdown)
        
        # Write to CSV
        csv_file = self.performance_dir / f'equity_curve_{self.session_start.strftime("%Y%m%d")}.csv'
        file_exists = csv_file.exists()
        
        with open(csv_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=snapshot.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(snapshot)
    
    def log_daily_pnl(self, date: str, realized_pnl: float, unrealized_pnl: float, trades_count: int):
        """Log daily P&L summary."""
        daily_data = {
            'date': date,
            'realized_pnl': realized_pnl,
            'unrealized_pnl': unrealized_pnl,
            'total_pnl': realized_pnl + unrealized_pnl,
            'trades_count': trades_count,
        }
        self.daily_pnl.append(daily_data)
        
        csv_file = self.performance_dir / f'daily_pnl_{self.session_start.strftime("%Y%m%d")}.csv'
        file_exists = csv_file.exists()
        
        with open(csv_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=daily_data.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(daily_data)
    
    def log_market_regime(self, symbol: str, regime_data: Dict):
        """
        Log market regime data for post-trade analysis.
        
        regime_data should include:
        - timestamp: datetime
        - symbol: str
        - atr: float (current volatility)
        - atr_percent: float (ATR as % of price)
        - trend_direction: str (UP/DOWN/SIDEWAYS)
        - trend_strength: float (0-1)
        - volatility_regime: str (LOW/MEDIUM/HIGH)
        - volume_regime: str (LOW/MEDIUM/HIGH)
        - rsi: float (if available)
        - ema_trend: str (if available)
        - price_range_pct: float (daily range as %)
        """
        regime_data['logged_at'] = datetime.now(timezone.utc).isoformat()
        self.regime_data.append(regime_data)
        
        csv_file = self.regime_dir / f'regime_{self.session_start.strftime("%Y%m%d")}.csv'
        file_exists = csv_file.exists()
        
        with open(csv_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=regime_data.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(regime_data)
    
    def log_infrastructure_event(self, event_type: str, event_data: Dict):
        """
        Log infrastructure events for monitoring.
        
        event_type: str (e.g., 'MT5_DISCONNECT', 'ORDER_REJECT', 'API_ERROR', 'RECONNECT')
        event_data: Dict with event details
        """
        event = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'event_type': event_type,
            'session_duration_hours': (datetime.now(timezone.utc) - self.session_start).total_seconds() / 3600,
        }
        event.update(event_data)
        self.infrastructure_events.append(event)
        
        csv_file = self.infrastructure_dir / f'events_{self.session_start.strftime("%Y%m%d")}.csv'
        file_exists = csv_file.exists()
        
        with open(csv_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=event.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(event)
    
    def calculate_performance_metrics(self) -> Dict:
        """Calculate comprehensive performance metrics."""
        if not self.equity_curve:
            return {}
        
        equity_values = [e['equity'] for e in self.equity_curve]
        initial_equity = equity_values[0]
        final_equity = equity_values[-1]
        total_return = (final_equity - initial_equity) / initial_equity * 100 if initial_equity > 0 else 0
        
        # Calculate daily returns
        if len(equity_values) > 1:
            returns = pd.Series(equity_values).pct_change().dropna()
            sharpe_ratio = returns.mean() / returns.std() * np.sqrt(252 * 24) if returns.std() > 0 else 0
            max_drawdown = max(self.drawdown_curve)
        else:
            sharpe_ratio = 0
            max_drawdown = 0
        
        # Calculate win rate from closed trades
        total_trades = sum(p['trades'] for p in self.strategy_performance.values())
        total_wins = sum(p['wins'] for p in self.strategy_performance.values())
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
        
        return {
            'session_start': self.session_start.isoformat(),
            'session_duration_hours': (datetime.now(timezone.utc) - self.session_start).total_seconds() / 3600,
            'initial_equity': initial_equity,
            'final_equity': final_equity,
            'total_return_pct': total_return,
            'max_drawdown_pct': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'total_trades': total_trades,
            'total_wins': total_wins,
            'win_rate_pct': win_rate,
            'strategy_count': len(self.strategy_performance),
            'infrastructure_events': len(self.infrastructure_events),
        }
    
    def generate_summary_report(self) -> str:
        """Generate a comprehensive summary report."""
        metrics = self.calculate_performance_metrics()
        
        report = []
        report.append("=" * 80)
        report.append("HEDGE FUND DATA COLLECTION SUMMARY")
        report.append("=" * 80)
        report.append(f"Session Start: {metrics.get('session_start', 'N/A')}")
        report.append(f"Session Duration: {metrics.get('session_duration_hours', 0):.2f} hours")
        report.append("")
        report.append("PERFORMANCE METRICS:")
        report.append(f"  Initial Equity: ${metrics.get('initial_equity', 0):.2f}")
        report.append(f"  Final Equity: ${metrics.get('final_equity', 0):.2f}")
        report.append(f"  Total Return: {metrics.get('total_return_pct', 0):.2f}%")
        report.append(f"  Max Drawdown: {metrics.get('max_drawdown_pct', 0):.2f}%")
        report.append(f"  Sharpe Ratio: {metrics.get('sharpe_ratio', 0):.2f}")
        report.append("")
        report.append("TRADING STATISTICS:")
        report.append(f"  Total Trades: {metrics.get('total_trades', 0)}")
        report.append(f"  Total Wins: {metrics.get('total_wins', 0)}")
        report.append(f"  Win Rate: {metrics.get('win_rate_pct', 0):.2f}%")
        report.append(f"  Strategies Tracked: {metrics.get('strategy_count', 0)}")
        report.append("")
        report.append("INFRASTRUCTURE:")
        report.append(f"  Events Logged: {metrics.get('infrastructure_events', 0)}")
        report.append("")
        report.append("STRATEGY PERFORMANCE:")
        for label, perf in self.strategy_performance.items():
            report.append(f"  {label}:")
            report.append(f"    Symbol: {perf['symbol']}")
            report.append(f"    Strategy: {perf['strategy']}")
            report.append(f"    Trades: {perf['trades']}")
            report.append(f"    Wins: {perf['wins']}")
            report.append(f"    Losses: {perf['losses']}")
            report.append(f"    Win Rate: {(perf['wins']/perf['trades']*100) if perf['trades'] > 0 else 0:.2f}%")
            report.append(f"    Avg Return: {perf['avg_return']:.2f}%")
            report.append(f"    Max Profit: {perf['max_profit']:.2f}")
            report.append(f"    Max Loss: {perf['max_loss']:.2f}")
            report.append(f"    Avg Holding Period: {perf['avg_holding_period']:.2f} minutes")
        
        report.append("")
        report.append("=" * 80)
        
        return "\n".join(report)
    
    def save_json_snapshot(self):
        """Save a complete JSON snapshot of all collected data."""
        snapshot = {
            'metadata': {
                'session_start': self.session_start.isoformat(),
                'snapshot_time': datetime.now(timezone.utc).isoformat(),
                'session_duration_hours': (datetime.now(timezone.utc) - self.session_start).total_seconds() / 3600,
            },
            'performance_metrics': self.calculate_performance_metrics(),
            'strategy_performance': self.strategy_performance,
            'equity_curve': self.equity_curve[-100:] if len(self.equity_curve) > 100 else self.equity_curve,  # Last 100 points
            'infrastructure_events': self.infrastructure_events[-50:] if len(self.infrastructure_events) > 50 else self.infrastructure_events,  # Last 50 events
        }
        
        json_file = self.base_dir / f'snapshot_{self.session_start.strftime("%Y%m%d_%H%M%S")}.json'
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, indent=2, default=str)
        
        return json_file


# Global instance for easy access
_collector_instance: Optional[HedgeFundDataCollector] = None


def get_data_collector() -> HedgeFundDataCollector:
    """Get or create the global data collector instance."""
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = HedgeFundDataCollector()
    return _collector_instance


def init_data_collector(base_dir: Path = None) -> HedgeFundDataCollector:
    """Initialize the global data collector with optional base directory."""
    global _collector_instance
    _collector_instance = HedgeFundDataCollector(base_dir)
    return _collector_instance
