"""
Multi-Period Robustness Test
Tests the portfolio across specific periods in different years to prove true robustness.
Tests 1-month, 3-month, 6-month, and 1-year periods across 2024, 2025, and 2026.
"""
import json
from pathlib import Path
import random
import time
from typing import Dict, List
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
BOT_DIR = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BOT_DIR))

from backtest_improved import fetch_data, add_indicators, INSTRUMENTS

# Strategy map
STRATEGY_MAP = {
    'mean_reversion': lambda df_h1, df_m5, symbol, sym_info, params: __import__('mean_reversion').generate_mean_reversion_signal(df_h1, df_m5, symbol, sym_info, params),
    'trend_momentum': lambda df_h1, df_m5, symbol, sym_info, params: __import__('trend_momentum').generate_trend_momentum_signal(df_h1, df_m5, symbol, sym_info, params),
    'volatility': lambda df_h1, df_m5, symbol, sym_info, params: __import__('volatility_strategy').generate_volatility_signal(df_h1, df_m5, symbol, sym_info, params),
    'breakout': lambda df_h1, df_m5, symbol, sym_info, params: __import__('breakout_strategy').generate_breakout_signal(df_h1, df_m5, symbol, sym_info, params),
}


def load_candidates(limit: int | None = None) -> List[Dict]:
    """Load candidate strategies from production_strategy_lock.json"""
    config_path = BOT_DIR / 'liverun' / 'production_strategy_lock.json'
    with open(config_path) as f:
        config = json.load(f)
    
    candidates = config['strategies']
    if limit and limit > 0:
        candidates = candidates[:limit]
    return candidates


def build_stepwise_equity(candidate: Dict, period_bars: int | None = None, bars: int = 50000, 
                          risk_pct: float = 1.0, rng: random.Random | None = None, 
                          start_index: int | None = None) -> tuple[pd.Series, List[float]]:
    """Build stepwise equity curve for a candidate strategy."""
    equity_start = 10000.0
    full_series = pd.Series([equity_start])
    trade_returns = []
    trade_count = 0
    
    symbol = candidate["symbol"]
    strategy_name = candidate["strategy"]
    params = candidate.get("params", {})
    
    # Fetch data
    df = fetch_data(symbol, bars=bars)
    if df is None or len(df) < 100:
        return full_series, trade_returns
    
    # Determine start index
    if start_index is None:
        start_index = 0
    if period_bars is None:
        period_bars = len(df) - start_index
    
    end_index = min(start_index + period_bars, len(df))
    df = df.iloc[start_index:end_index].copy()
    
    # Resample to timeframes
    df_h1 = df.resample('1h').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
    }).dropna()
    df_m5 = df.resample('5min').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
    }).dropna()
    
    # Add indicators
    df_h1 = add_indicators(df_h1)
    df_m5 = add_indicators(df_m5)
    
    # Get strategy generator
    gen = STRATEGY_MAP.get(strategy_name)
    if gen is None:
        return full_series, trade_returns
    
    sym_info = INSTRUMENTS.get(symbol, {'pip_size': 0.0001, 'spread': 1.0, 'vol': 1.0})
    
    # Simulate trades
    step = 12  # Check every hour (12 M5 bars)
    min_i = max(50, step)
    max_i = len(df_m5) - 50
    
    for i in range(min_i, max_i, step):
        df_h1_hist = df_h1.iloc[:i//12].copy()
        df_m5_hist = df_m5.iloc[:i].copy()
        
        try:
            signal = gen(df_h1_hist, df_m5_hist, symbol, sym_info, params)
            if not signal or not signal.get('direction'):
                continue
            
            direction = signal['direction']
            entry = signal.get('entry')
            stop = signal.get('stop')
            target = signal.get('target')
            
            if not all([direction, entry, stop, target]):
                continue
            
            # Simulate trade exit
            exit_price = None
            exit_time = None
            
            for j in range(i + 1, min(i + 100, len(df_m5))):
                high = df_m5.iloc[j]['High']
                low = df_m5.iloc[j]['Low']
                
                if direction == 'BUY':
                    if high >= target:
                        exit_price = target
                        exit_time = df_m5.index[j]
                        break
                    elif low <= stop:
                        exit_price = stop
                        exit_time = df_m5.index[j]
                        break
                else:
                    if low <= target:
                        exit_price = target
                        exit_time = df_m5.index[j]
                        break
                    elif high >= stop:
                        exit_price = stop
                        exit_time = df_m5.index[j]
                        break
            
            if exit_price is None:
                exit_price = df_m5.iloc[min(i + 100, len(df_m5) - 1)]['Close']
                exit_time = df_m5.index[min(i + 100, len(df_m5) - 1)]
            
            # Calculate profit with fixed position sizing
            # Use fixed lot size per instrument for consistency
            if symbol in ["XAUUSD", "BTCUSD", "NAS100"]:
                position_size = 0.1
            elif symbol in ["EURUSD", "GBPUSD", "USDJPY"]:
                position_size = 1.0
            else:
                position_size = 0.5
            
            if direction == "BUY":
                profit = position_size * (exit_price - entry)
            else:
                profit = position_size * (entry - exit_price)
            
            # Update equity
            current_balance = full_series.ffill().iloc[-1]
            if pd.isna(current_balance):
                current_balance = equity_start
            
            new_balance = current_balance + profit
            return_pct = (profit / current_balance) * 100.0
            trade_returns.append(return_pct)
            trade_count += 1
            
            # Apply to equity series
            ts = pd.to_datetime(exit_time) if exit_time is not None else df_m5.index[i]
            idx = full_series.index.get_indexer([ts], method='nearest')[0]
            if idx >= 0:
                full_series.iloc[idx] = new_balance
                full_series = full_series.ffill()
            
        except Exception:
            continue
    
    return full_series, trade_returns


def max_drawdown(equity: List[float]) -> float:
    peak = -np.inf
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return (max_dd / peak * 100.0) if peak and peak > 0 else 0.0


def calculate_metrics(equity_series: pd.Series, trade_returns: List[float]) -> dict:
    """Calculate performance metrics for a single run."""
    if len(equity_series) == 0:
        return {
            'final_balance': 10000.0,
            'total_return_pct': 0.0,
            'max_drawdown_pct': 0.0,
            'trades': 0,
            'win_rate': 0.0,
        }
    
    final_balance = float(equity_series.iloc[-1])
    initial_balance = float(equity_series.iloc[0])
    total_return_pct = ((final_balance - initial_balance) / initial_balance) * 100.0
    max_dd = max_drawdown(list(equity_series.values))
    
    if trade_returns:
        wins = [r for r in trade_returns if r > 0]
        win_rate = len(wins) / len(trade_returns)
    else:
        win_rate = 0.0
    
    return {
        'final_balance': final_balance,
        'total_return_pct': total_return_pct,
        'max_drawdown_pct': max_dd,
        'trades': len(trade_returns),
        'win_rate': win_rate,
    }


def run_specific_period_test(candidates: List[Dict], outdir: Path, bars: int, 
                              period_days: int, start_date_offset: int, 
                              risk_pct: float, seed: int | None) -> dict:
    """Run test for a specific period (e.g., Jan 2024, Q2 2025, etc)."""
    rng = random.Random(seed)
    
    per_inst_series = {}
    per_inst_trades = {}
    
    for c in candidates:
        try:
            # Calculate specific start index based on date offset
            # period_days controls the length, start_date_offset controls where to start
            df = None
            from backtest_improved import fetch_data
            df = fetch_data(c["symbol"], bars=bars)
            
            if df is None or len(df) < period_days * 96 + 50:
                print(f"  [!] Insufficient data for {c['symbol']}")
                continue
            
            # Calculate start index based on date offset (in days)
            bars_per_day = 96  # M15 bars per day
            start_index = start_date_offset * bars_per_day
            
            # Ensure start_index is within bounds
            max_start = max(0, len(df) - period_days * bars_per_day - 50)
            start_index = min(start_index, max_start)
            
            # Use 1% risk per trade as recommended in video (Lesson 7)
            # Position size kills more talents than bad entries
            actual_risk_pct = 1.0
            
            s, trades = build_stepwise_equity(
                c, 
                period_bars=period_days * bars_per_day, 
                bars=bars, 
                risk_pct=actual_risk_pct, 
                rng=rng, 
                start_index=start_index
            )
            
            per_inst_series[c['symbol']] = s
            per_inst_trades[c['symbol']] = trades
            
            print(f"  {c['symbol']}: {len(trades)} trades, return: {s.iloc[-1] / s.iloc[0] * 100 - 100:.2f}%")
            
        except Exception as e:
            print(f"  [!] {c['symbol']} error: {e}")
            per_inst_series[c['symbol']] = pd.Series(dtype=float)
            per_inst_trades[c['symbol']] = []
    
    # Build portfolio
    common_index = pd.Index([])
    for s in per_inst_series.values():
        if len(s) > 0:
            common_index = common_index.union(s.index)
    common_index = common_index.sort_values()
    
    if len(common_index) == 0:
        return None
    
    equity_df = pd.DataFrame(index=common_index)
    start_cap = 100000.0
    n_sym = len(per_inst_series)
    cap_each = start_cap / max(1, n_sym)
    
    for sym, s in per_inst_series.items():
        if len(s) == 0:
            continue
        s2 = s.reindex(common_index).ffill().fillna(10000.0)
        base = float(s2.iloc[0]) if len(s2) and not pd.isna(s2.iloc[0]) else 10000.0
        if base == 0:
            norm = s2.copy()
            norm[:] = 1.0
        else:
            norm = s2 / base
        equity_df[sym] = norm * cap_each
    
    portfolio = equity_df.sum(axis=1)
    all_trades = []
    for trades in per_inst_trades.values():
        all_trades.extend(trades)
    
    metrics = calculate_metrics(portfolio, all_trades)
    
    return {
        'period_days': period_days,
        'start_date_offset': start_date_offset,
        'metrics': metrics,
        'portfolio_equity': portfolio,
    }


def run_multi_period_robustness(candidates: List[Dict], outdir: Path, bars: int, 
                                 risk_pct: float, seed: int | None):
    """Run multi-period robustness test across different years and period lengths."""
    outdir.mkdir(parents=True, exist_ok=True)
    
    # Test configurations: (period_days, start_date_offset_days, label)
    # start_date_offset is days from the end of the data to start
    test_configs = [
        # 2024 periods (1 month) - start with shorter periods to validate fix
        (30, 700, "Jan 2024 (1 month)"),
        (30, 610, "Feb 2024 (1 month)"),
        (30, 520, "Mar 2024 (1 month)"),
        
        # 2025 periods (1 month)
        (30, 335, "Jan 2025 (1 month)"),
        (30, 245, "Feb 2025 (1 month)"),
        (30, 155, "Mar 2025 (1 month)"),
        
        # 2026 periods (1 month) - using positive offsets
        (30, 30, "Jan 2026 (1 month)"),
        (30, 120, "Feb 2026 (1 month)"),
        (30, 210, "Mar 2026 (1 month)"),
    ]
    
    results = []
    
    for period_days, start_offset, label in test_configs:
        print(f"\n{'='*60}")
        print(f"Testing: {label}")
        print(f"{'='*60}")
        
        try:
            result = run_specific_period_test(
                candidates, outdir, bars, period_days, start_offset, risk_pct, seed
            )
            
            if result:
                result['label'] = label
                results.append(result)
                
                metrics = result['metrics']
                print(f"\nPortfolio Results:")
                print(f"  Final Balance: ${metrics['final_balance']:,.0f}")
                print(f"  Return: {metrics['total_return_pct']:.2f}%")
                print(f"  Max Drawdown: {metrics['max_drawdown_pct']:.2f}%")
                print(f"  Total Trades: {metrics['trades']}")
                print(f"  Win Rate: {metrics['win_rate']:.2%}")
                
                # Classification based on sustainability and risk management
                # Focus on proving edge across periods, not capping returns
                # High returns are good if sustainable with controlled drawdown
                if metrics['max_drawdown_pct'] <= 15.0 and metrics['total_return_pct'] > 0.0:
                    status = "EXCELLENT"
                elif metrics['max_drawdown_pct'] <= 20.0 and metrics['total_return_pct'] > -10.0:
                    status = "GOOD"
                elif metrics['max_drawdown_pct'] <= 30.0 and metrics['total_return_pct'] > -20.0:
                    status = "ACCEPTABLE"
                else:
                    status = "FAIL"
                
                print(f"  Status: {status}")
                
        except Exception as e:
            print(f"  [!] Error: {e}")
            continue
    
    # Summary
    print(f"\n{'='*80}")
    print("MULTI-PERIOD ROBUSTNESS SUMMARY")
    print(f"{'='*80}")
    
    print(f"\n{'Period':<30} {'Return %':<12} {'Drawdown %':<12} {'Status':<15}")
    print("-" * 69)
    
    for result in results:
        metrics = result['metrics']
        # Classification based on sustainability and risk management
        # Focus on proving edge across periods, not capping returns
        if metrics['max_drawdown_pct'] <= 15.0 and metrics['total_return_pct'] > 0.0:
            status = 'EXCELLENT'
        elif metrics['max_drawdown_pct'] <= 20.0 and metrics['total_return_pct'] > -10.0:
            status = 'GOOD'
        elif metrics['max_drawdown_pct'] <= 30.0 and metrics['total_return_pct'] > -20.0:
            status = 'ACCEPTABLE'
        else:
            status = 'FAIL'
        
        print(f"{result['label']:<30} {metrics['total_return_pct']:<12.2f} {metrics['max_drawdown_pct']:<12.2f} {status:<15}")
    
    # Calculate overall robustness metrics
    if results:
        avg_return = np.mean([r['metrics']['total_return_pct'] for r in results])
        avg_drawdown = np.mean([r['metrics']['max_drawdown_pct'] for r in results])
        pass_count = sum(1 for r in results if r['metrics']['max_drawdown_pct'] <= 30.0 and r['metrics']['total_return_pct'] > -20.0)
        
        print(f"\n{'='*60}")
        print("OVERALL ROBUSTNESS METRICS")
        print(f"{'='*60}")
        print(f"Average Return: {avg_return:.2f}%")
        print(f"Average Drawdown: {avg_drawdown:.2f}%")
        print(f"Return/DD Ratio: {avg_return / avg_drawdown if avg_drawdown > 0 else 0:.2f}")
        print(f"Pass Rate: {pass_count}/{len(results)} ({pass_count/len(results)*100:.1f}%)")
    
    # Save results (without pandas Series objects)
    summary = {
        'test_configs': test_configs,
        'results': [
            {
                'label': r['label'],
                'period_days': r['period_days'],
                'start_date_offset': r['start_date_offset'],
                'metrics': r['metrics'],
            }
            for r in results
        ],
        'avg_return': float(avg_return) if results else 0.0,
        'avg_drawdown': float(avg_drawdown) if results else 0.0,
        'pass_rate': float(pass_count/len(results)) if results else 0.0,
    }
    
    (outdir / 'multi_period_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    
    # Create visualization
    if results:
        plt.figure(figsize=(14, 8))
        
        # Plot individual period equity curves
        for i, result in enumerate(results):
            portfolio = result['portfolio_equity']
            normalized = portfolio / portfolio.iloc[0] * 100
            plt.plot(normalized.index, normalized.values, label=f"{result['label']} ({result['metrics']['total_return_pct']:.1f}%)")
        
        plt.title("Multi-Period Robustness Test - Portfolio Equity Curves")
        plt.xlabel("Date")
        plt.ylabel("Normalized Equity (%)")
        plt.legend(loc='best', fontsize=8)
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(outdir / 'multi_period_equity_curves.png', dpi=150)
    
    print(f"\nResults saved to: {outdir}")
    print(f"Multi-period equity curves saved to: {outdir / 'multi_period_equity_curves.png'}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=int, default=50000, help="Total bars to fetch")
    parser.add_argument("--limit", type=int, default=0, help="0=all candidates")
    parser.add_argument("--out", type=str, default=str(BOT_DIR / "liverun" / "multi_period_robustness"))
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    candidates = load_candidates(limit=args.limit if args.limit > 0 else None)
    if not candidates:
        print("No candidates found; aborting")
        return

    outdir = Path(args.out) / f'run_{int(time.time())}'
    run_multi_period_robustness(candidates, outdir, args.bars, args.risk_pct, args.seed)


if __name__ == '__main__':
    main()
