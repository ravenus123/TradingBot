"""
Institutional-Grade Edge Validation Framework
Following Gemini methodology for robust edge testing
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import sys

sys.path.insert(0, str(Path(__file__).parent))

from monte_carlo_robustness import fetch_data, add_indicators, STRATEGY_MAP, build_stepwise_equity, max_drawdown, calculate_metrics

class EdgeValidator:
    """
    Institutional-grade edge validation following Gemini methodology:
    1. Out-of-Sample (OOS) Testing
    2. Parameter Sensitivity Analysis (Plateaus vs Peaks)
    3. Monte Carlo with Risk Metrics (Sharpe/Sortino)
    4. Correlation Analysis with Existing Portfolio
    5. Walk-Forward Analysis
    """
    
    def __init__(self, symbol: str, strategy_config: dict):
        self.symbol = symbol
        self.strategy_name = strategy_config['strategy']
        self.params = strategy_config['params']
        self.generator = STRATEGY_MAP.get(self.strategy_name)
        
    def split_data_oos(self, df: pd.DataFrame, oos_ratio: float = 0.2) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Step 1: Out-of-Sample (OOS) Split
        Split data into In-Sample (80%) and Out-of-Sample (20%) buckets
        """
        split_point = int(len(df) * (1 - oos_ratio))
        
        is_data = df.iloc[:split_point].copy()
        oos_data = df.iloc[split_point:].copy()
        
        print(f"OOS Split: IS={len(is_data)} bars, OOS={len(oos_data)} bars")
        return is_data, oos_data
    
    def parameter_sensitivity_analysis(self, df: pd.DataFrame, param_ranges: Dict[str, List]) -> Dict:
        """
        Step 2: Parameter Sensitivity Testing
        Look for plateaus (robust) vs peaks (fragile)
        """
        print("\n=== Parameter Sensitivity Analysis ===")
        
        results = {}
        param_grid = []
        
        # Generate parameter combinations
        keys = list(param_ranges.keys())
        values = list(param_ranges.values())
        
        for combo in np.ndindex(*[len(v) for v in values]):
            params = self.params.copy()
            for i, key in enumerate(keys):
                params[key] = values[i][combo[i]]
            param_grid.append(params)
        
        print(f"Testing {len(param_grid)} parameter combinations...")
        
        for i, test_params in enumerate(param_grid):
            equity, trades = build_stepwise_equity(
                {'symbol': self.symbol, 'strategy': self.strategy_name, 'params': test_params},
                df,
                risk_pct=0.2
            )
            
            metrics = calculate_metrics(equity, trades)
            results[f"combo_{i}"] = {
                'params': test_params,
                'return_pct': metrics['total_return_pct'],
                'sharpe': metrics.get('sharpe_ratio', 0),
                'max_drawdown': metrics.get('max_drawdown', 0)
            }
        
        # Analyze results for plateaus vs peaks
        returns = [r['return_pct'] for r in results.values()]
        sharpe_ratios = [r['sharpe'] for r in results.values()]
        
        return {
            'parameter_grid': results,
            'return_std': np.std(returns),
            'return_mean': np.mean(returns),
            'sharpe_std': np.std(sharpe_ratios),
            'is_plateau': np.std(returns) / np.mean(returns) < 0.5 if np.mean(returns) > 0 else False
        }
    
    def calculate_institutional_metrics(self, equity: pd.Series, trades: List[float]) -> Dict:
        """
        Calculate institutional-grade metrics per Gemini framework
        """
        if len(equity) < 2 or len(trades) == 0:
            return {}
        
        equity_array = np.array(equity)
        returns = np.diff(equity_array) / equity_array[:-1]
        returns = returns[~np.isnan(returns)]
        
        # Filter out zero returns
        returns = returns[returns != 0]
        
        if len(returns) == 0:
            return {}
        
        # Sharpe Ratio
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
        
        # Sortino Ratio (only downside volatility)
        downside_returns = returns[returns < 0]
        downside_std = np.std(downside_returns) if len(downside_returns) > 0 else 1e-10
        sortino = np.mean(returns) / downside_std * np.sqrt(252)
        
        # Profit Factor
        gross_profit = sum([t for t in trades if t > 0])
        gross_loss = abs(sum([t for t in trades if t < 0]))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Mathematical Expectancy
        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t < 0]
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        win_rate = len(wins) / len(trades) if trades else 0
        loss_rate = len(losses) / len(trades) if trades else 0
        
        expectancy = (win_rate * avg_win) - (loss_rate * abs(avg_loss))
        
        # Max Drawdown Duration
        peak = 0
        max_duration = 0
        current_duration = 0
        peak_idx = 0
        
        for i in range(len(equity_array)):
            if equity_array[i] > peak:
                peak = equity_array[i]
                peak_idx = i
                current_duration = 0
            else:
                current_duration = i - peak_idx
                if current_duration > max_duration:
                    max_duration = current_duration
        
        return {
            'sharpe': sharpe,
            'sortino': sortino,
            'profit_factor': profit_factor,
            'mathematical_expectancy': expectancy,
            'max_drawdown_duration': max_duration,
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss
        }
    
    def correlation_analysis(self, candidate_equity: pd.Series, benchmark_equity: pd.Series) -> Dict:
        """
        Step 4: Correlation Analysis with Existing Portfolio
        Check if new strategy is uncorrelated with existing strategies
        """
        print("\n=== Correlation Analysis ===")
        
        # Align lengths
        min_len = min(len(candidate_equity), len(benchmark_equity))
        candidate_returns = candidate_equity.iloc[:min_len].pct_change().dropna()
        benchmark_returns = benchmark_equity.iloc[:min_len].pct_change().dropna()
        
        # Align indices
        candidate_returns, benchmark_returns = candidate_returns.align(benchmark_returns, join='inner')
        
        if len(candidate_returns) < 10:
            return {'correlation': 1.0, 'status': 'INSUFFICIENT_DATA'}
        
        correlation = candidate_returns.corr(benchmark_returns)
        
        # Interpret correlation
        if abs(correlation) > 0.7:
            status = "HIGHLY_CORRELATED - REJECT"
        elif abs(correlation) > 0.3:
            status = "MODERATELY_CORRELATED - PROCEED WITH CAUTION"
        elif abs(correlation) > 0.2:
            status = "SLIGHTLY_CORRELATED - ACCEPTABLE"
        else:
            status = "UNCORRELATED - OPTIMAL"
        
        return {
            'correlation': correlation,
            'status': status,
            'is_good': abs(correlation) < 0.3
        }
    
    def validate_edge(self, df: pd.DataFrame, benchmark_strategy: str = 'bollinger') -> Dict:
        """
        Complete edge validation following Gemini methodology
        """
        print(f"\n{'='*60}")
        print(f"INSTITUTIONAL-GRADE EDGE VALIDATION")
        print(f"Strategy: {self.strategy_name} on {self.symbol}")
        print(f"{'='*60}")
        
        results = {
            'strategy': self.strategy_name,
            'symbol': self.symbol,
            'is_valid': False,
            'tests': {}
        }
        
        # Step 1: OOS Testing
        print("\nStep 1: Out-of-Sample Testing...")
        is_data, oos_data = self.split_data_oos(df)
        
        # Test on IS data
        is_equity, is_trades = build_stepwise_equity(
            {'symbol': self.symbol, 'strategy': self.strategy_name, 'params': self.params},
            is_data,
            risk_pct=0.2
        )
        is_metrics = calculate_metrics(is_equity, is_trades)
        is_institutional = self.calculate_institutional_metrics(is_equity, is_trades)
        
        # Test on OOS data (THE GOLDMINE TEST)
        oos_equity, oos_trades = build_stepwise_equity(
            {'symbol': self.symbol, 'strategy': self.strategy_name, 'params': self.params},
            oos_data,
            risk_pct=0.2
        )
        oos_metrics = calculate_metrics(oos_equity, oos_trades)
        oos_institutional = self.calculate_institutional_metrics(oos_equity, oos_trades)
        
        # OOS vs IS comparison
        oos_performance_drop = (is_metrics['total_return_pct'] - oos_metrics['total_return_pct']) / abs(is_metrics['total_return_pct']) if is_metrics['total_return_pct'] != 0 else 0
        
        oos_pass = oos_metrics['total_return_pct'] > 0 and oos_performance_drop < 0.5
        
        results['tests']['oos'] = {
            'is_return': is_metrics['total_return_pct'],
            'oos_return': oos_metrics['total_return_pct'],
            'oos_sharpe': oos_institutional.get('sharpe', 0),
            'oos_sortino': oos_institutional.get('sortino', 0),
            'oos_profit_factor': oos_institutional.get('profit_factor', 0),
            'performance_drop': oos_performance_drop,
            'pass': oos_pass
        }
        
        print(f"  IS Return: {is_metrics['total_return_pct']:.2f}%")
        print(f"  OOS Return: {oos_metrics['total_return_pct']:.2f}%")
        print(f"  Performance Drop: {oos_performance_drop:.2%}")
        print(f"  OOS Sharpe: {oos_institutional.get('sharpe', 0):.2f}")
        print(f"  OOS Pass: {oos_pass}")
        
        # Step 2: Parameter Sensitivity (example on momentum threshold)
        print("\nStep 2: Parameter Sensitivity Analysis...")
        param_ranges = {
            'momentum_threshold': [0.3, 0.4, 0.5, 0.6, 0.7],
            'lookback': [15, 18, 20, 22, 25]
        }
        sensitivity = self.parameter_sensitivity_analysis(df, param_ranges)
        
        results['tests']['sensitivity'] = {
            'is_plateau': sensitivity['is_plateau'],
            'return_std': sensitivity['return_std'],
            'return_mean': sensitivity['return_mean'],
            'pass': sensitivity['is_plateau']
        }
        
        print(f"  Is Plateau (Robust): {sensitivity['is_plateau']}")
        print(f"  Return Std: {sensitivity['return_std']:.2f}%")
        print(f"  Sensitivity Pass: {sensitivity['is_plateau']}")
        
        # Step 3: Institutional Metrics on OOS
        print("\nStep 3: Institutional Metrics (OOS)...")
        institutional_metrics = self.calculate_institutional_metrics(oos_equity, oos_trades)
        
        # Institutional thresholds
        sharpe_pass = institutional_metrics.get('sharpe', 0) > 1.5
        sortino_pass = institutional_metrics.get('sortino', 0) > 1.5
        profit_factor_pass = 1.3 < institutional_metrics.get('profit_factor', 0) < 2.0
        expectancy_pass = institutional_metrics.get('mathematical_expectancy', 0) > 0
        
        results['tests']['institutional'] = {
            'sharpe': institutional_metrics.get('sharpe', 0),
            'sortino': institutional_metrics.get('sortino', 0),
            'profit_factor': institutional_metrics.get('profit_factor', 0),
            'expectancy': institutional_metrics.get('mathematical_expectancy', 0),
            'max_drawdown_duration': institutional_metrics.get('max_drawdown_duration', 0),
            'sharpe_pass': sharpe_pass,
            'sortino_pass': sortino_pass,
            'profit_factor_pass': profit_factor_pass,
            'expectancy_pass': expectancy_pass,
            'pass': sharpe_pass and sortino_pass and expectancy_pass
        }
        
        print(f"  Sharpe: {institutional_metrics.get('sharpe', 0):.2f} (Target >1.5)")
        print(f"  Sortino: {institutional_metrics.get('sortino', 0):.2f} (Target >1.5)")
        print(f"  Profit Factor: {institutional_metrics.get('profit_factor', 0):.2f} (Target 1.3-2.0)")
        print(f"  Expectancy: {institutional_metrics.get('mathematical_expectancy', 0):.4f}")
        print(f"  Institutional Pass: {sharpe_pass and sortino_pass and expectancy_pass}")
        
        # Step 4: Correlation with existing strategy
        print("\nStep 4: Correlation Analysis...")
        
        # Get benchmark strategy performance
        benchmark_config = {
            'symbol': self.symbol,
            'strategy': benchmark_strategy,
            'params': {'period': 20, 'std_dev': 2.0, 'stop_atr_mult': 0.6, 'tp_atr_mult': 1.5}
        }
        
        benchmark_generator = STRATEGY_MAP.get(benchmark_strategy)
        if benchmark_generator:
            benchmark_equity, _ = build_stepwise_equity(benchmark_config, df, risk_pct=0.2)
            correlation_results = self.correlation_analysis(oos_equity, benchmark_equity)
        else:
            correlation_results = {'correlation': 0.5, 'status': 'BENCHMARK_NOT_FOUND', 'is_good': False}
        
        results['tests']['correlation'] = correlation_results
        print(f"  Correlation with {benchmark_strategy}: {correlation_results.get('correlation', 0):.3f}")
        print(f"  Status: {correlation_results.get('status', 'UNKNOWN')}")
        
        # Final Decision
        print(f"\n{'='*60}")
        print("FINAL VALIDATION DECISION")
        print(f"{'='*60}")
        
        all_pass = (
            results['tests']['oos']['pass'] and
            results['tests']['sensitivity']['pass'] and
            results['tests']['institutional']['pass'] and
            correlation_results.get('is_good', False)
        )
        
        results['is_valid'] = all_pass
        
        if all_pass:
            print("✅ GOLDMINE FOUND - Strategy passes all institutional tests")
            print("   - OOS Performance: Robust")
            print("   - Parameter Sensitivity: Plateau (not fragile peak)")
            print("   - Risk Metrics: Sharpe >1.5, Sortino >1.5")
            print("   - Correlation: Low/Uncorrelated with existing portfolio")
        else:
            print("❌ STRATEGY FAILED - Not a robust edge")
            if not results['tests']['oos']['pass']:
                print("   - FAILED: OOS performance too weak")
            if not results['tests']['sensitivity']['pass']:
                print("   - FAILED: Parameter sensitivity shows fragile peak")
            if not results['tests']['institutional']['pass']:
                print("   - FAILED: Risk metrics below institutional thresholds")
            if not correlation_results.get('is_good', False):
                print("   - FAILED: Too correlated with existing portfolio")
        
        return results

def run_edge_validation():
    """Run institutional-grade validation on simple_momentum strategy"""
    
    # Test simple_momentum on XAUUSD
    validator = EdgeValidator('XAUUSD', {
        'strategy': 'simple_momentum',
        'params': {
            'lookback': 20,
            'momentum_threshold': 0.5,
            'volume_confirmation': True,
            'stop_atr_mult': 1.5,
            'tp_atr_mult': 3.0
        }
    })
    
    # Fetch data
    from backtest_improved import INSTRUMENTS
    df = fetch_data('XAUUSD', bars=3000)
    
    if df is not None:
        # Run validation
        results = validator.validate_edge(df, benchmark_strategy='bollinger')
        
        # Save results
        output_dir = Path(__file__).parent / 'liverun' / 'edge_validation'
        output_dir.mkdir(parents=True, exist_ok=True)
        
        with open(output_dir / 'simple_momentum_validation.json', 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nValidation results saved to: {output_dir / 'simple_momentum_validation.json'}")
        
        return results
    else:
        print("Failed to fetch data")
        return None

if __name__ == '__main__':
    results = run_edge_validation()
    print(json.dumps(results, indent=2))
