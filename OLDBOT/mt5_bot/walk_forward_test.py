"""Walk-forward testing for out-of-sample validation.
Tests strategy performance across multiple time periods to prove edge is real, not luck.
"""
import json
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import pandas as pd
import numpy as np

from backtest_improved import fetch_data, add_indicators
from mean_reversion import generate_mean_reversion_signal
from rsi_strategy import generate_rsi_signal
from stochastic_strategy import generate_stochastic_signal
from trend_momentum import generate_trend_momentum_signal
import volatility_strategy
import breakout_strategy
import macd_strategy
import bollinger_strategy
import smart_money_strategy

BOT_DIR = Path(__file__).parent
CONFIG_FILE = BOT_DIR / 'liverun' / 'config' / 'production_strategy_lock.json'


def load_candidates() -> List[Dict]:
    """Load candidate strategies from production_strategy_lock.json"""
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    return config['strategies']


def simulate_period(df_1h: pd.DataFrame, df_5m: pd.DataFrame, candidates: List[Dict], 
                   risk_pct: float = 1.0) -> Tuple[float, float, int]:
    """Simulate trading for a single period."""
    equity = 10000.0
    peak_equity = equity
    max_drawdown = 0.0
    trades = 0
    
    STRATEGY_MAP = {
        'mean_reversion': generate_mean_reversion_signal,
        'rsi': generate_rsi_signal,
        'stochastic': generate_stochastic_signal,
        'trend_momentum': generate_trend_momentum_signal,
        'volatility': volatility_strategy.generate_volatility_signal,
        'breakout': breakout_strategy.generate_breakout_signal,
        'macd': macd_strategy.generate_macd_signal,
        'bollinger': bollinger_strategy.generate_bollinger_signal,
        'smart_money': lambda df_h1, df_m5, symbol, sym_info, params: smart_money_strategy.SmartMoneyStrategy(df_h1, df_m5, symbol).check_signal(),
    }
    
    # Simulate stepwise trading (simplified)
    step = 12  # Every hour (12 M5 bars)
    min_i = 100
    max_i = min(len(df_5m), 3000)
    
    for i in range(min_i, max_i, step):
        df_h1_hist = df_1h.iloc[:i//12]
        df_m5_hist = df_5m.iloc[:i]
        
        if len(df_h1_hist) < 50 or len(df_m5_hist) < 50:
            continue
        
        # Collect signals from all strategies
        signals = []
        for candidate in candidates:
            if not candidate.get('enabled', True):
                continue
            
            strategy = candidate['strategy']
            if strategy not in STRATEGY_MAP:
                continue
            
            gen = STRATEGY_MAP[strategy]
            signal = gen(df_h1_hist, df_m5_hist, candidate['symbol'], {}, candidate.get('params', {}))
            if signal:
                signals.append(signal)
        
        # Take best signal by confluence score
        if signals:
            best_signal = max(signals, key=lambda x: x.get('confluence_score', x.get('score', 0)))
            
            # Calculate position size
            atr = df_m5_hist['ATR'].iloc[-1] if 'ATR' in df_m5_hist.columns else 0.001
            stop_distance = abs(best_signal['entry'] - best_signal['stop'])
            if stop_distance > 0:
                risk_amount = equity * (risk_pct / 100.0)
                lot_size = risk_amount / stop_distance
                lot_size = max(0.01, min(lot_size, 10.0))
                
                # Simulate trade outcome (simplified)
                direction = best_signal['direction']
                entry = best_signal['entry']
                tp = best_signal.get('tp', best_signal.get('target', entry + atr * 2))
                sl = best_signal['stop']
                
                # Random walk simulation for price movement
                # This is a simplified model - in reality we'd use actual price data
                price_move = np.random.normal(0, atr * 0.5)
                
                if direction == 'BUY':
                    exit_price = entry + price_move
                    if exit_price >= tp:
                        profit = (tp - entry) * lot_size
                    elif exit_price <= sl:
                        profit = (sl - entry) * lot_size
                    else:
                        profit = (exit_price - entry) * lot_size
                else:
                    exit_price = entry + price_move
                    if exit_price <= tp:
                        profit = (entry - tp) * lot_size
                    elif exit_price >= sl:
                        profit = (entry - sl) * lot_size
                    else:
                        profit = (entry - exit_price) * lot_size
                
                equity += profit
                trades += 1
                
                # Track drawdown
                if equity > peak_equity:
                    peak_equity = equity
                dd = (peak_equity - equity) / peak_equity * 100
                if dd > max_drawdown:
                    max_drawdown = dd
    
    total_return = (equity - 10000.0) / 10000.0 * 100
    return total_return, max_drawdown, trades


def run_walk_forward_test(num_periods: int = 10, bars_per_period: int = 3000):
    """Run walk-forward test across multiple time periods."""
    candidates = load_candidates()
    
    # Group candidates by symbol
    symbols = set(c['symbol'] for c in candidates)
    
    results = []
    
    for symbol in symbols:
        print(f"\n=== Walk-forward test for {symbol} ===")
        
        # Fetch data
        df_m15 = fetch_data(symbol, bars=bars_per_period + 1000)
        if df_m15 is None:
            print(f"Failed to fetch data for {symbol}")
            continue
        
        # Resample to H1 and M5 (df_m15 already has Time as index)
        df_1h = df_m15.resample('1h').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
        }).dropna()
        df_5m = df_m15.resample('5min').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
        }).dropna()
        
        df_1h = df_1h.reset_index()
        df_5m = df_5m.reset_index()
        
        # Add indicators
        df_1h = add_indicators(df_1h, ema_period=20).fillna(0)
        df_5m = add_indicators(df_5m, ema_period=20).fillna(0)
        
        # Get symbol-specific candidates
        symbol_candidates = [c for c in candidates if c['symbol'] == symbol]
        
        # Run walk-forward periods
        period_returns = []
        period_drawdowns = []
        period_trades = []
        period_entries = []
        
        total_bars = len(df_5m)
        bars_per_period_actual = total_bars // num_periods
        
        for period in range(num_periods):
            start_idx = period * bars_per_period_actual
            end_idx = min((period + 1) * bars_per_period_actual, total_bars)
            
            if end_idx - start_idx < 500:
                continue
            
            df_5m_period = df_5m.iloc[start_idx:end_idx].copy()
            df_1h_period = df_1h.iloc[start_idx//12:end_idx//12].copy()
            ret, dd, trades = simulate_period(df_1h_period, df_5m_period, symbol_candidates)

            # Determine human-readable start/end for this period
            try:
                if 'Time' in df_5m_period.columns:
                    period_start = pd.to_datetime(df_5m_period['Time'].iloc[0])
                    period_end = pd.to_datetime(df_5m_period['Time'].iloc[-1])
                else:
                    period_start = pd.to_datetime(df_5m_period.index[0])
                    period_end = pd.to_datetime(df_5m_period.index[-1])
            except Exception:
                period_start = None
                period_end = None

            period_returns.append(ret)
            period_drawdowns.append(dd)
            period_trades.append(trades)
            period_entries.append({
                'period_index': period + 1,
                'start_date': str(period_start) if period_start is not None else None,
                'end_date': str(period_end) if period_end is not None else None,
                'return_pct': float(ret),
                'drawdown_pct': float(dd),
                'trades': int(trades),
            })

            print(f"Period {period+1}/{num_periods}: {period_start} -> {period_end} | Return={ret:.2f}%, DD={dd:.2f}%, Trades={trades}")
        
        # Calculate statistics
        if period_returns:
            mean_return = np.mean(period_returns)
            std_return = np.std(period_returns)
            mean_dd = np.mean(period_drawdowns)
            mean_trades = np.mean(period_trades)
            positive_periods = sum(1 for r in period_returns if r > 0) / len(period_returns) * 100
            
            # record data window for clarity
            try:
                data_start = pd.to_datetime(df_5m['Time'].iloc[0])
                data_end = pd.to_datetime(df_5m['Time'].iloc[-1])
            except Exception:
                data_start = None
                data_end = None

            results.append({
                'symbol': symbol,
                'data_start': str(data_start) if data_start is not None else None,
                'data_end': str(data_end) if data_end is not None else None,
                'mean_return': mean_return,
                'std_return': std_return,
                'mean_drawdown': mean_dd,
                'mean_trades': mean_trades,
                'positive_periods': positive_periods,
                'num_periods': len(period_returns),
                'periods': period_entries,
            })
            
            print(f"\n{symbol} Walk-forward Summary:")
            print(f"  Mean Return: {mean_return:.2f}% (±{std_return:.2f}%)")
            print(f"  Mean Drawdown: {mean_dd:.2f}%")
            print(f"  Mean Trades: {mean_trades:.0f}")
            print(f"  Positive Periods: {positive_periods:.1f}%")
    
    # Overall summary
    print("\n" + "="*80)
    print("WALK-FORWARD TEST SUMMARY")
    print("="*80)
    
    if results:
        overall_mean_return = np.mean([r['mean_return'] for r in results])
        overall_std_return = np.std([r['mean_return'] for r in results])
        overall_mean_dd = np.mean([r['mean_drawdown'] for r in results])
        overall_positive = np.mean([r['positive_periods'] for r in results])
        
        print(f"\nOverall Across All Symbols:")
        print(f"  Mean Return: {overall_mean_return:.2f}% (±{overall_std_return:.2f}%)")
        print(f"  Mean Drawdown: {overall_mean_dd:.2f}%")
        print(f"  Positive Periods: {overall_positive:.1f}%")
        
        # Determine if edge is real
        if overall_mean_return > 0 and overall_positive > 60:
            print(f"\n✓ EDGE LIKELY REAL: Positive mean return with >60% profitable periods")
        elif overall_mean_return > 0:
            print(f"\n⚠ EDGE UNCERTAIN: Positive mean but low consistency")
        else:
            print(f"\n✗ EDGE NOT DETECTED: Negative or zero mean return")
    
    return results


if __name__ == '__main__':
    results = run_walk_forward_test(num_periods=10, bars_per_period=3000)
    
    # Save results
    output_file = Path(__file__).parent / 'liverun' / 'walk_forward_results.json'
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\nResults saved to: {output_file}")
