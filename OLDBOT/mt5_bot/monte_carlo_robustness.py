"""
Monte Carlo Robustness Test
Tests the portfolio with random start/end dates to validate robustness.
This aligns with lesson 9: Overfitting is the enemy - need out-of-sample testing.
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
    'rsi': lambda df_h1, df_m5, symbol, sym_info, params: __import__('rsi_strategy').generate_rsi_signal(df_h1, df_m5, symbol, sym_info, params),
    'macd': lambda df_h1, df_m5, symbol, sym_info, params: __import__('macd_strategy').generate_macd_signal(df_h1, df_m5, symbol, sym_info, params),
    'bollinger': lambda df_h1, df_m5, symbol, sym_info, params: __import__('bollinger_strategy').generate_bollinger_signal(df_h1, df_m5, symbol, sym_info, params),
    'stochastic': lambda df_h1, df_m5, symbol, sym_info, params: __import__('stochastic_strategy').generate_stochastic_signal(df_h1, df_m5, symbol, sym_info, params),
    'smart_money': lambda df_h1, df_m5, symbol, sym_info, params: __import__('smart_money_strategy').SmartMoneyStrategy(df_h1, df_m5, symbol).check_signal(),
}


def load_candidates(limit: int | None = None, config_file: str = 'production_strategy_lock.json') -> List[Dict]:
    """Load candidate strategies from config file"""
    config_path = BOT_DIR / 'liverun' / 'config' / config_file
    with open(config_path) as f:
        config = json.load(f)
    
    candidates = config['strategies']
    if limit and limit > 0:
        candidates = candidates[:limit]
    return candidates


def build_stepwise_equity(candidate: Dict, df: pd.DataFrame, risk_pct: float = 1.0) -> tuple[pd.Series, List[float]]:
    """Build stepwise equity curve for a candidate strategy."""
    equity_start = 10000.0
    trade_returns = []
    trade_count = 0
    equity_current = float(equity_start)
    equity_progress = [equity_current]
    
    symbol = candidate["symbol"]
    strategy_name = candidate["strategy"]
    params = candidate.get("params", {})
    
    if df is None or len(df) < 100:
        return pd.Series([equity_start]), trade_returns
    
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
        return pd.Series([equity_start]), trade_returns
    
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
            
            direction = signal.get('direction')
            entry = signal.get('entry')
            stop = signal.get('stop')
            target = signal.get('target') or signal.get('tp')
            
            if not all([direction, entry, stop, target]):
                continue
            
            # Simulate trade exit with realistic costs
            exit_price = None
            exit_time = None
            
            # Get instrument-specific costs
            pip_size = sym_info.get('pip_size', 0.0001)
            spread_pips = sym_info.get('spread', 1.0)
            spread_cost = spread_pips * pip_size
            commission = 0.00007  # $7 per lot round trip (typical for major pairs)
            slippage_pips = 0.5  # 0.5 pips slippage on average
            slippage_cost = slippage_pips * pip_size
            
            # Adjust entry for costs (spread + slippage)
            entry_with_costs = entry + spread_cost + slippage_cost if direction == 'BUY' else entry - spread_cost - slippage_cost
            
            for j in range(i + 1, min(i + 100, len(df_m5))):
                high = df_m5.iloc[j]['High']
                low = df_m5.iloc[j]['Low']
                
                if direction == 'BUY':
                    if high >= target:
                        exit_price = target - slippage_cost  # Slippage on exit
                        exit_time = df_m5.index[j]
                        break
                    elif low <= stop:
                        exit_price = stop + slippage_cost  # Slippage on exit
                        exit_time = df_m5.index[j]
                        break
                else:
                    if low <= target:
                        exit_price = target + slippage_cost  # Slippage on exit
                        exit_time = df_m5.index[j]
                        break
                    elif high >= stop:
                        exit_price = stop - slippage_cost  # Slippage on exit
                        exit_time = df_m5.index[j]
                        break
            
            if exit_price is None:
                exit_price = df_m5.iloc[min(i + 100, len(df_m5) - 1)]['Close']
                exit_time = df_m5.index[min(i + 100, len(df_m5) - 1)]
            
            # Calculate profit with risk-based position sizing (use history-local ATR)
            atr = df_m5_hist.iloc[-1]['ATR'] if 'ATR' in df_m5_hist.columns else 0.001

            # Determine stop distance and risk amount (risk_pct is percent)
            stop_distance = abs(entry - stop)
            if stop_distance <= 0:
                continue

            risk_amount = equity_current * (risk_pct / 100.0)

            # Position size in price units such that loss = stop_distance * position_size
            position_size = risk_amount / stop_distance if stop_distance > 0 else 0.0

            # Cap position size to reasonable bounds
            position_size = min(position_size, 100.0)
            position_size = max(position_size, 0.01)

            if direction == "BUY":
                profit = position_size * (exit_price - entry_with_costs) - commission * position_size
            else:
                profit = position_size * (entry_with_costs - exit_price) - commission * position_size

            # Track trade returns relative to current equity and update equity
            return_pct = (profit / equity_current) * 100.0
            trade_returns.append(return_pct)
            trade_count += 1
            equity_current += profit
            equity_progress.append(float(equity_current))
            
        except Exception:
            continue
    
    # If no trades executed, return starting equity
    if len(equity_progress) == 1:
        return pd.Series([equity_start]), trade_returns

    full_series = pd.Series(equity_progress)
    return full_series, trade_returns


def max_drawdown(equity: List[float]) -> float:
    if not equity:
        return 0.0
    arr = np.array(equity, dtype=float)
    running_max = np.maximum.accumulate(arr)
    drawdowns = running_max - arr
    return float(np.nanmax(drawdowns))


def calculate_metrics(equity: pd.Series, trades: List[float]) -> Dict:
    if len(equity) == 0:
        return {
            'final_balance': 10000.0,
            'total_return_pct': 0.0,
            'max_drawdown_pct': 0.0,
            'trades': 0,
            'win_rate': 0.0,
        }
    final_balance = float(equity.iloc[-1])
    starting_balance = float(equity.iloc[0])
    total_return_pct = ((final_balance - starting_balance) / starting_balance) * 100.0
    max_dd_abs = max_drawdown(equity.tolist())
    max_dd_pct = (max_dd_abs / starting_balance) * 100.0 if starting_balance > 0 else 0.0

    wins = sum(1 for t in trades if t > 0)
    win_rate = wins / len(trades) if trades else 0.0

    return {
        'final_balance': float(final_balance),
        'total_return_pct': float(total_return_pct),
        'max_drawdown_pct': float(max_dd_pct),
        'trades': len(trades),
        'win_rate': float(win_rate),
    }


def run_monte_carlo_test(candidates: List[Dict], outdir: Path, bars: int = 50000, 
                          num_simulations: int = 50, period_days: int = 30, 
                          risk_pct: float = 1.0, seed: int | None = None, 
                          config_file: str = 'production_strategy_lock.json') -> Dict:
    """Run Monte Carlo robustness test with random start/end dates."""
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
    
    results = []
    strategy_performance = {c['strategy']: [] for c in candidates}
    
    for sim in range(num_simulations):
        print(f"Simulation {sim + 1}/{num_simulations}")
        
        # Random start date (within last 2 years of data)
        max_start_offset = bars - (period_days * 96)  # 96 M15 bars per day
        start_offset = random.randint(0, max_start_offset)
        
        # Fetch data for all instruments
        instrument_data = {}
        for symbol in set(c["symbol"] for c in candidates):
            df = fetch_data(symbol, bars=bars)
            if df is not None and len(df) >= 100:
                # Slice to random period
                end_index = min(start_offset + period_days * 96, len(df))
                df = df.iloc[start_offset:end_index].copy()
                instrument_data[symbol] = df
        
        if not instrument_data:
            continue
        
        # Run all candidates on this period
        portfolio_equity = pd.Series([10000.0])
        all_trades = []
        
        for candidate in candidates:
            symbol = candidate["symbol"]
            strategy_name = candidate["strategy"]
            if symbol not in instrument_data:
                continue
            
            equity, trades = build_stepwise_equity(candidate, instrument_data[symbol], risk_pct)
            
            # Track individual strategy performance
            strategy_metrics = calculate_metrics(equity, trades)
            strategy_performance[strategy_name].append(strategy_metrics['total_return_pct'])
            
            if len(equity) > 1:
                # Combine equity curves
                if len(portfolio_equity) == 1:
                    portfolio_equity = equity
                else:
                    # Align and sum
                    min_len = min(len(portfolio_equity), len(equity))
                    portfolio_equity = portfolio_equity.iloc[:min_len] + (equity.iloc[:min_len] - 10000.0)
                
                all_trades.extend(trades)
        
        metrics = calculate_metrics(portfolio_equity, all_trades)
        metrics['simulation'] = sim + 1
        metrics['start_offset'] = start_offset
        # record actual start/end timestamps for this simulation period
        try:
            starts = [df.index[0] for df in instrument_data.values()]
            ends = [df.index[-1] for df in instrument_data.values()]
            overall_start = min(starts)
            overall_end = max(ends)
            metrics['start_date'] = str(pd.to_datetime(overall_start))
            metrics['end_date'] = str(pd.to_datetime(overall_end))
        except Exception:
            metrics['start_date'] = None
            metrics['end_date'] = None
        
        results.append(metrics)
        
        sd = metrics.get('start_date')
        ed = metrics.get('end_date')
        if sd and ed:
            print(f"  Period: {sd} -> {ed}")
        print(f"  Return: {metrics['total_return_pct']:.2f}%, Drawdown: {metrics['max_drawdown_pct']:.2f}%, Trades: {metrics['trades']}")
    
    # Analyze individual strategy performance
    strategy_stats = {}
    for strategy, returns_list in strategy_performance.items():
        if returns_list:
            strategy_stats[strategy] = {
                'mean_return': float(np.mean(returns_list)),
                'std_return': float(np.std(returns_list)),
                'positive_rate': float(sum(1 for r in returns_list if r > 0) / len(returns_list)),
                'sample_size': len(returns_list)
            }
    
    return {
        'results': results,
        'num_simulations': num_simulations,
        'period_days': period_days,
        'strategy_performance': strategy_stats,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=int, default=50000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=str, default=str(BOT_DIR / "liverun" / "monte_carlo_robustness"))
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--simulations", type=int, default=50)
    parser.add_argument("--period-days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--config", type=str, default="production_strategy_lock.json")
    args = parser.parse_args()

    candidates = load_candidates(limit=args.limit if args.limit > 0 else None, config_file=args.config)
    if not candidates:
        print("No candidates found; aborting")
        return

    outdir = Path(args.out) / f'run_{int(time.time())}'
    outdir.mkdir(parents=True, exist_ok=True)
    
    print(f"Running Monte Carlo robustness test with {args.simulations} simulations")
    print(f"Period: {args.period_days} days per simulation")
    print(f"Candidates: {len(candidates)}")
    
    result = run_monte_carlo_test(
        candidates, outdir, args.bars, args.simulations, args.period_days, args.risk_pct, args.seed, args.config
    )
    
    # Calculate statistics
    returns = [r['total_return_pct'] for r in result['results']]
    drawdowns = [r['max_drawdown_pct'] for r in result['results']]
    trades = [r['trades'] for r in result['results']]
    
    print(f"\n{'='*80}")
    print("MONTE CARLO ROBUSTNESS SUMMARY")
    print(f"{'='*80}")
    print(f"Simulations: {len(result['results'])}")
    print(f"Return - Mean: {np.mean(returns):.2f}%, Std: {np.std(returns):.2f}%, Min: {np.min(returns):.2f}%, Max: {np.max(returns):.2f}%")
    print(f"Drawdown - Mean: {np.mean(drawdowns):.2f}%, Std: {np.std(drawdowns):.2f}%, Max: {np.max(drawdowns):.2f}%")
    print(f"Trades - Mean: {np.mean(trades):.0f}, Std: {np.std(trades):.0f}")
    
    # Individual strategy performance
    print(f"\n{'='*80}")
    print("INDIVIDUAL STRATEGY PERFORMANCE")
    print(f"{'='*80}")
    for strategy, stats in result['strategy_performance'].items():
        print(f"{strategy}:")
        print(f"  Mean Return: {stats['mean_return']:.2f}%")
        print(f"  Std Return: {stats['std_return']:.2f}%")
        print(f"  Positive Rate: {stats['positive_rate']:.1%}")
        print(f"  Sample Size: {stats['sample_size']}")
    
    # Classification
    positive_returns = sum(1 for r in returns if r > 0)
    pass_rate = positive_returns / len(returns) if returns else 0
    
    if pass_rate >= 0.7 and np.mean(returns) > 0:
        status = "EXCELLENT"
    elif pass_rate >= 0.5 and np.mean(returns) > -5:
        status = "GOOD"
    else:
        status = "FAIL"
    
    print(f"\nPositive Return Rate: {pass_rate:.1%}")
    print(f"Overall Status: {status}")
    
    # Save results
    summary = {
        'config': {
            'simulations': args.simulations,
            'period_days': args.period_days,
            'risk_pct': args.risk_pct,
            'seed': args.seed,
        },
        'results': result['results'],
        'strategy_performance': result['strategy_performance'],
        'statistics': {
            'return_mean': float(np.mean(returns)),
            'return_std': float(np.std(returns)),
            'return_min': float(np.min(returns)),
            'return_max': float(np.max(returns)),
            'drawdown_mean': float(np.mean(drawdowns)),
            'drawdown_std': float(np.std(drawdowns)),
            'drawdown_max': float(np.max(drawdowns)),
            'trades_mean': float(np.mean(trades)),
            'positive_return_rate': float(pass_rate),
            'status': status,
        }
    }
    
    (outdir / 'monte_carlo_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    
    # Plot return distribution
    plt.figure(figsize=(12, 6))
    plt.hist(returns, bins=20, edgecolor='black', alpha=0.7)
    plt.axvline(np.mean(returns), color='red', linestyle='--', label=f'Mean: {np.mean(returns):.2f}%')
    plt.axvline(0, color='green', linestyle='-', label='Break Even')
    plt.xlabel('Return %')
    plt.ylabel('Frequency')
    plt.title(f'Monte Carlo Return Distribution ({args.simulations} Simulations)')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.savefig(outdir / 'return_distribution.png', dpi=150)
    plt.close()
    
    print(f"\nResults saved to: {outdir}")


if __name__ == '__main__':
    main()
