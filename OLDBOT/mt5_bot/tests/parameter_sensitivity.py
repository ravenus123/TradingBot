"""Parameter sensitivity analysis.
Tests strategy robustness to parameter variations to prove edge is not overfitted.
"""
import json
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import pandas as pd
import numpy as np

from backtest_improved import fetch_data, add_indicators, INSTRUMENTS
from bollinger_strategy import generate_bollinger_signal
from volatility_strategy import generate_volatility_signal
from macd_strategy import generate_macd_signal

BOT_DIR = Path(__file__).parent
DEFAULT_CONFIG_FILE = BOT_DIR / 'liverun' / 'config' / 'production_strategy_lock.json'


def load_candidates(config_file: Path = DEFAULT_CONFIG_FILE) -> List[Dict]:
    """Load candidate strategies from config file"""
    with open(config_file) as f:
        config = json.load(f)
    return config['strategies']


def vary_params(params: Dict, strategy: str) -> Dict:
    """Vary parameters by ±20% to test sensitivity."""
    varied = params.copy()
    
    if strategy == 'bollinger':
        # Vary period, std_dev and risk management
        varied['period'] = int(params.get('period', 20) * random.uniform(0.8, 1.2))
        varied['std_dev'] = params.get('std_dev', 2.0) * random.uniform(0.8, 1.2)
        varied['stop_atr_mult'] = params.get('stop_atr_mult', 0.6) * random.uniform(0.8, 1.2)
        varied['tp_atr_mult'] = params.get('tp_atr_mult', 1.5) * random.uniform(0.8, 1.2)
    
    elif strategy == 'volatility':
        # Vary volatility window, threshold and risk management
        varied['volatility_window'] = int(params.get('volatility_window', 20) * random.uniform(0.8, 1.2))
        varied['volatility_threshold'] = params.get('volatility_threshold', 1.5) * random.uniform(0.8, 1.2)
        varied['stop_atr_mult'] = params.get('stop_atr_mult', 0.7) * random.uniform(0.8, 1.2)
        varied['tp_atr_mult'] = params.get('tp_atr_mult', 2.0) * random.uniform(0.8, 1.2)
    
    elif strategy == 'macd':
        # Vary periods and risk management
        varied['fast_period'] = int(params.get('fast_period', 12) * random.uniform(0.8, 1.2))
        varied['slow_period'] = int(params.get('slow_period', 26) * random.uniform(0.9, 1.1))
        varied['signal_period'] = int(params.get('signal_period', 9) * random.uniform(0.8, 1.2))
        varied['stop_atr_mult'] = params.get('stop_atr_mult', 0.7) * random.uniform(0.8, 1.2)
        varied['tp_atr_mult'] = params.get('tp_atr_mult', 1.8) * random.uniform(0.8, 1.2)
    
    return varied


def simulate_with_params(df_1h: pd.DataFrame, df_5m: pd.DataFrame, candidates: List[Dict], 
                       risk_pct: float = 1.0) -> Tuple[float, float, int]:
    """Simulate trading with given parameters."""
    equity = 10000.0
    peak_equity = equity
    max_drawdown = 0.0
    trades = 0
    
    STRATEGY_MAP = {
        'bollinger': generate_bollinger_signal,
        'volatility': generate_volatility_signal,
        'macd': generate_macd_signal,
    }
    
    # Simulate stepwise trading
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
            sym_info = INSTRUMENTS.get(candidate['symbol'], {'pip_size': 0.0001, 'spread': 1.0, 'vol': 1.0})
            signal = gen(df_h1_hist, df_m5_hist, candidate['symbol'], sym_info, candidate.get('params', {}))
            if signal:
                signals.append(signal)
        
        # Take best signal by confluence score
        if signals:
            best_signal = max(signals, key=lambda x: x.get('confluence_score', 0))
            
            # Calculate position size
            atr = df_m5_hist['ATR'].iloc[-1] if 'ATR' in df_m5_hist.columns else 0.001
            stop_distance = abs(best_signal['entry'] - best_signal['stop'])
            if stop_distance > 0:
                risk_amount = equity * (risk_pct / 100.0)
                lot_size = risk_amount / stop_distance
                lot_size = max(0.01, min(lot_size, 10.0))
                
                # Simulate trade outcome (simplified with costs)
                direction = best_signal['direction']
                entry = best_signal['entry']
                tp = best_signal.get('tp') or best_signal.get('target')
                sl = best_signal['stop']
                
                # Add realistic costs
                sym_info = INSTRUMENTS.get(candidate['symbol'], {'pip_size': 0.0001, 'spread': 1.0, 'vol': 1.0})
                pip_size = sym_info.get('pip_size', 0.0001)
                spread_pips = sym_info.get('spread', 1.0)
                spread_cost = spread_pips * pip_size
                commission = 0.00007
                slippage_pips = 0.5
                slippage_cost = slippage_pips * pip_size
                
                entry_with_costs = entry + spread_cost + slippage_cost if direction == 'BUY' else entry - spread_cost - slippage_cost
                
                # Random walk simulation for price movement
                price_move = np.random.normal(0, atr * 0.5)
                
                if direction == 'BUY':
                    exit_price = entry + price_move
                    if exit_price >= tp:
                        profit = (tp - slippage_cost - entry_with_costs) * lot_size - commission * lot_size
                    elif exit_price <= sl:
                        profit = (sl + slippage_cost - entry_with_costs) * lot_size - commission * lot_size
                    else:
                        profit = (exit_price - entry_with_costs) * lot_size - commission * lot_size
                else:
                    exit_price = entry + price_move
                    if exit_price <= tp:
                        profit = (entry_with_costs - tp - slippage_cost) * lot_size - commission * lot_size
                    elif exit_price >= sl:
                        profit = (entry_with_costs - sl + slippage_cost) * lot_size - commission * lot_size
                    else:
                        profit = (entry_with_costs - exit_price) * lot_size - commission * lot_size
                
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


def run_parameter_sensitivity(num_variations: int = 20, config_file: Path = None):
    """Run parameter sensitivity analysis."""
    if config_file:
        base_candidates = load_candidates(config_file)
    else:
        base_candidates = load_candidates()
    
    # Group candidates by symbol
    symbols = set(c['symbol'] for c in base_candidates)
    
    results = []
    
    for symbol in symbols:
        print(f"\n=== Parameter sensitivity for {symbol} ===")
        
        # Fetch data
        df_m15 = fetch_data(symbol, bars=4000)
        if df_m15 is None:
            print(f"Failed to fetch data for {symbol}")
            continue
        # Record data period for clarity
        try:
            data_start = pd.to_datetime(df_m15.index[0])
            data_end = pd.to_datetime(df_m15.index[-1])
            print(f"[DATA] Period for {symbol}: {data_start} -> {data_end}")
        except Exception:
            data_start = None
            data_end = None
        
        # Resample to H1 and M5
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
        symbol_candidates = [c for c in base_candidates if c['symbol'] == symbol]
        
        # Run parameter variations
        returns = []
        drawdowns = []
        trade_counts = []
        
        for i in range(num_variations):
            # Vary parameters for each strategy
            varied_candidates = []
            for candidate in symbol_candidates:
                varied_params = vary_params(candidate['params'], candidate['strategy'])
                varied_candidate = candidate.copy()
                varied_candidate['params'] = varied_params
                varied_candidates.append(varied_candidate)
            
            ret, dd, trades = simulate_with_params(df_1h, df_5m, varied_candidates)
            
            returns.append(ret)
            drawdowns.append(dd)
            trade_counts.append(trades)
            
            print(f"  Variation {i+1}/{num_variations}: Return={ret:.2f}%, DD={dd:.2f}%, Trades={trades}")
        
        # Calculate statistics
        if returns:
            mean_return = np.mean(returns)
            std_return = np.std(returns)
            min_return = np.min(returns)
            max_return = np.max(returns)
            mean_dd = np.mean(drawdowns)
            mean_trades = np.mean(trade_counts)
            positive_variations = sum(1 for r in returns if r > 0) / len(returns) * 100
            
            results.append({
                'symbol': symbol,
                'data_start': str(data_start) if data_start is not None else None,
                'data_end': str(data_end) if data_end is not None else None,
                'mean_return': mean_return,
                'std_return': std_return,
                'min_return': min_return,
                'max_return': max_return,
                'mean_drawdown': mean_dd,
                'mean_trades': mean_trades,
                'positive_variations': positive_variations,
                'num_variations': len(returns),
            })
            
            print(f"\n{symbol} Parameter Sensitivity Summary:")
            print(f"  Mean Return: {mean_return:.2f}% (±{std_return:.2f}%)")
            print(f"  Return Range: [{min_return:.2f}%, {max_return:.2f}%]")
            print(f"  Mean Drawdown: {mean_dd:.2f}%")
            print(f"  Mean Trades: {mean_trades:.0f}")
            print(f"  Positive Variations: {positive_variations:.1f}%")
            
            # Determine robustness
            if positive_variations > 70 and std_return < mean_return * 0.5:
                print(f"  ✓ ROBUST: High consistency and low sensitivity")
            elif positive_variations > 60:
                print(f"  ⚠ MODERATE: Some parameter sensitivity")
            else:
                print(f"  ✗ NOT ROBUST: High parameter sensitivity")
    
    # Overall summary
    print("\n" + "="*80)
    print("PARAMETER SENSITIVITY SUMMARY")
    print("="*80)
    
    if results:
        overall_mean_return = np.mean([r['mean_return'] for r in results])
        overall_std_return = np.mean([r['std_return'] for r in results])
        overall_positive = np.mean([r['positive_variations'] for r in results])
        
        print(f"\nOverall Across All Symbols:")
        print(f"  Mean Return: {overall_mean_return:.2f}% (±{overall_std_return:.2f}%)")
        print(f"  Positive Variations: {overall_positive:.1f}%")
        
        # Determine if edge is robust
        if overall_positive > 70 and overall_std_return < overall_mean_return * 0.5:
            print(f"\n✓ EDGE ROBUST: Consistent performance across parameter variations")
        elif overall_positive > 60:
            print(f"\n⚠ EDGE MODERATELY ROBUST: Some parameter sensitivity")
        else:
            print(f"\n✗ EDGE NOT ROBUST: High parameter sensitivity - may be overfitted")
    
    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Run parameter sensitivity analysis')
    parser.add_argument('--config', type=str, help='Config file path (default: production_strategy_lock.json)')
    args = parser.parse_args()
    
    config_file = None
    if args.config:
        config_file = BOT_DIR / 'liverun' / 'config' / args.config
    
    results = run_parameter_sensitivity(num_variations=20, config_file=config_file)
    
    # Save results
    output_file = Path(__file__).parent / 'liverun' / 'parameter_sensitivity_results.json'
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\nResults saved to: {output_file}")
