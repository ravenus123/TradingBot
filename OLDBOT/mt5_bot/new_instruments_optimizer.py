"""
New Instruments Optimization
Tests GBPUSD, USDJPY, BTCUSD with different strategies to find profitable combinations.
Goal: Expand portfolio from 3 to 5+ instruments.
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

from backtest_improved import fetch_data, add_indicators, INSTRUMENTS
from trend_momentum import generate_trend_momentum_signal
from mean_reversion import generate_mean_reversion_signal
from volatility_breakout import generate_volatility_breakout_signal

# Configuration
NEW_INSTRUMENTS = ['GBPUSD', 'USDJPY', 'BTCUSD']
STRATEGIES_TO_TEST = ['trend_momentum', 'mean_reversion', 'volatility_breakout']

RESULTS_DIR = Path(__file__).parent / 'liverun' / 'new_instruments_optimizer'
RESULTS_DIR.mkdir(exist_ok=True)

# Parameter grids optimized per strategy
PARAMETER_GRIDS = {
    'trend_momentum': [
        {'h1_ema_period': 34, 'm5_ema_period': 12, 'stop_atr_mult': 0.6},
        {'h1_ema_period': 34, 'm5_ema_period': 12, 'stop_atr_mult': 0.8},
        {'h1_ema_period': 34, 'm5_ema_period': 12, 'stop_atr_mult': 1.0},
        {'h1_ema_period': 34, 'm5_ema_period': 20, 'stop_atr_mult': 1.0},
    ],
    'mean_reversion': [
        {'window': 30, 'z_threshold': 2.0, 'stop_atr_mult': 0.8},
        {'window': 30, 'z_threshold': 2.5, 'stop_atr_mult': 0.8},
        {'window': 30, 'z_threshold': 2.0, 'stop_atr_mult': 1.0},
    ],
    'volatility_breakout': [
        {'bb_period': 20, 'bb_std': 2.0, 'kc_period': 20, 'kc_mult': 1.5, 'squeeze_bars': 5, 'stop_atr_mult': 1.5, 'rr_ratio': 2.0},
        {'bb_period': 20, 'bb_std': 2.0, 'kc_period': 20, 'kc_mult': 1.5, 'squeeze_bars': 5, 'stop_atr_mult': 2.0, 'rr_ratio': 2.0},
    ],
}

# Risk configuration
RISK_CONFIG = {
    'risk_per_trade_pct': 0.2,
    'min_lot_size': 0.01,
    'max_lot_size': 5.0,
    'max_leverage': 10.0,
    'initial_balance': 10000.0,
}

TRADING_COSTS = {
    'spread_pips': 1.0,
    'slippage_pips': 0.5,
    'commission_per_lot': 0.5,
}


def resample_to_timeframes(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Resample M15 data to H1 and M5 timeframes."""
    df_h1 = df.resample('1h').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    
    df_m5 = df.resample('5min').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    
    return df_h1, df_m5


def calculate_position_size(entry: float, stop: float, current_equity: float, 
                           risk_per_trade_pct: float) -> float:
    """Calculate position size with constraints."""
    risk_amount = current_equity * (risk_per_trade_pct / 100.0)
    stop_distance = abs(entry - stop)
    
    if stop_distance == 0:
        return RISK_CONFIG['min_lot_size']
    
    position_size = risk_amount / stop_distance
    position_size = max(RISK_CONFIG['min_lot_size'], 
                       min(position_size, RISK_CONFIG['max_lot_size']))
    
    leverage = (position_size * entry) / current_equity
    if leverage > RISK_CONFIG['max_leverage']:
        position_size = (RISK_CONFIG['max_leverage'] * current_equity) / entry
        position_size = max(RISK_CONFIG['min_lot_size'], 
                           min(position_size, RISK_CONFIG['max_lot_size']))
    
    return position_size


def apply_trading_costs(entry: float, exit_price: float, position_size: float, 
                       direction: str) -> Tuple[float, float, float]:
    """Apply trading costs to trade."""
    pip_value = 0.01
    spread_cost = TRADING_COSTS['spread_pips'] * pip_value
    if direction == 'BUY':
        actual_entry = entry + spread_cost
    else:
        actual_entry = entry - spread_cost
    
    slippage_cost = TRADING_COSTS['slippage_pips'] * pip_value
    if direction == 'BUY':
        actual_exit = exit_price - slippage_cost
    else:
        actual_exit = exit_price + slippage_cost
    
    commission_cost = TRADING_COSTS['commission_per_lot'] * position_size
    return actual_entry, actual_exit, commission_cost


def simulate_strategy(df_h1: pd.DataFrame, df_m5: pd.DataFrame, symbol: str, 
                     strategy_name: str, params: dict) -> List[dict]:
    """Simulate strategy with trading costs."""
    trades = []
    sym_info = INSTRUMENTS.get(symbol, {})
    
    min_len = min(len(df_h1), len(df_m5))
    equity = RISK_CONFIG['initial_balance']
    equity_curve = [equity]
    
    if strategy_name == 'trend_momentum':
        signal_func = generate_trend_momentum_signal
    elif strategy_name == 'mean_reversion':
        signal_func = generate_mean_reversion_signal
    elif strategy_name == 'volatility_breakout':
        signal_func = generate_volatility_breakout_signal
    else:
        return []
    
    for i in range(50, min_len - 10):
        df_h1_hist = df_h1.iloc[:i]
        df_m5_hist = df_m5.iloc[:i]
        
        if len(df_h1_hist) < 50 or len(df_m5_hist) < 30:
            continue
        
        signal = signal_func(df_h1_hist, df_m5_hist, symbol, sym_info, params)
        
        if signal is None:
            continue
        
        df_future = df_m5.iloc[i+1:i+100]
        if len(df_future) < 10:
            continue
        
        entry = signal['entry']
        stop = signal['stop']
        tp = signal['tp']
        direction = signal['direction']
        
        position_size = calculate_position_size(entry, stop, equity, RISK_CONFIG['risk_per_trade_pct'])
        actual_entry, _, commission = apply_trading_costs(entry, tp, position_size, direction)
        
        result = 'timeout'
        exit_price = entry
        
        for _, row in df_future.iterrows():
            high = row['High']
            low = row['Low']
            
            if direction == 'BUY':
                if high >= tp:
                    result = 'tp'
                    exit_price = tp
                    break
                elif low <= stop:
                    result = 'sl'
                    exit_price = stop
                    break
            else:
                if low <= tp:
                    result = 'tp'
                    exit_price = tp
                    break
                elif high >= stop:
                    result = 'sl'
                    exit_price = stop
                    break
        
        _, actual_exit, exit_commission = apply_trading_costs(entry, exit_price, position_size, direction)
        total_commission = commission + exit_commission
        
        if direction == 'BUY':
            pnl = position_size * (actual_exit - actual_entry) - total_commission
        else:
            pnl = position_size * (actual_entry - actual_exit) - total_commission
        
        actual_return_pct = (pnl / equity) * 100
        equity += pnl
        equity_curve.append(equity)
        
        trades.append({
            'result': result,
            'actual_return_pct': actual_return_pct,
            'position_size': position_size,
            'pnl': pnl,
            'commission': total_commission,
            'equity': equity,
        })
    
    peak = equity_curve[0]
    max_drawdown = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        drawdown = (peak - eq) / peak * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    
    trades.append({
        'equity_curve': equity_curve,
        'max_drawdown_pct': max_drawdown,
    })
    
    return trades


def calculate_metrics(trades: List[dict]) -> dict:
    """Calculate performance metrics."""
    if not trades:
        return {'total_trades': 0, 'win_rate': 0.0, 'avg_return_pct': 0.0, 'total_return_pct': 0.0, 'max_drawdown_pct': 0.0}
    
    equity_curve = trades[-1].get('equity_curve', [])
    max_drawdown = trades[-1].get('max_drawdown_pct', 0.0)
    trade_trades = [t for t in trades if 'equity_curve' not in t]
    
    if not trade_trades:
        return {'total_trades': 0, 'win_rate': 0.0, 'avg_return_pct': 0.0, 'total_return_pct': 0.0, 'max_drawdown_pct': max_drawdown}
    
    returns = [t['actual_return_pct'] for t in trade_trades]
    total_return_pct = ((equity_curve[-1] - equity_curve[0]) / equity_curve[0]) * 100 if equity_curve else 0.0
    wins = [r for r in returns if r > 0]
    
    return {
        'total_trades': len(trade_trades),
        'win_rate': len(wins) / len(trade_trades) if trade_trades else 0.0,
        'avg_return_pct': np.mean(returns) if returns else 0.0,
        'total_return_pct': total_return_pct,
        'max_drawdown_pct': max_drawdown,
    }


def run_new_instruments_optimization():
    """Run new instruments optimization."""
    print("=" * 80)
    print("NEW INSTRUMENTS OPTIMIZATION")
    print("=" * 80)
    print(f"\nInstruments to test: {NEW_INSTRUMENTS}")
    print(f"Strategies to test: {STRATEGIES_TO_TEST}")
    print(f"Risk per Trade: {RISK_CONFIG['risk_per_trade_pct']}%")
    
    all_results = []
    profitable_combinations = []
    
    for instrument in NEW_INSTRUMENTS:
        print(f"\n{'='*60}")
        print(f"INSTRUMENT: {instrument}")
        print(f"{'='*60}")
        
        try:
            df = fetch_data(instrument, bars=5000)
            if df is None or len(df) < 500:
                print(f"  [!] Insufficient data for {instrument}")
                continue
            
            start_date = df.index[0].strftime('%Y-%m-%d')
            end_date = df.index[-1].strftime('%Y-%m-%d')
            print(f"  Date Range: {start_date} to {end_date}")
            
            df_h1, df_m5 = resample_to_timeframes(df)
            
            for strategy in STRATEGIES_TO_TEST:
                print(f"\n  Testing {strategy}...")
                
                param_grid = PARAMETER_GRIDS.get(strategy, [])
                if not param_grid:
                    print(f"    [!] No parameter grid for {strategy}")
                    continue
                
                for params in param_grid:
                    trades = simulate_strategy(df_h1, df_m5, instrument, strategy, params)
                    metrics = calculate_metrics(trades)
                    
                    result = {
                        'instrument': instrument,
                        'strategy': strategy,
                        'params': params,
                        'metrics': metrics,
                    }
                    
                    all_results.append(result)
                    
                    print(f"    Params: {params}")
                    print(f"    Return: {metrics['total_return_pct']:.2f}%")
                    print(f"    Drawdown: {metrics['max_drawdown_pct']:.2f}%")
                    print(f"    Trades: {metrics['total_trades']}")
                    print(f"    Win Rate: {metrics['win_rate']:.2%}")
                    
                    if metrics['max_drawdown_pct'] <= 25.0 and metrics['total_return_pct'] > 10.0:
                        print(f"    [ACCEPTABLE] Meets acceptable hedge fund standards")
                        profitable_combinations.append(result)
                    elif metrics['max_drawdown_pct'] <= 20.0 and metrics['total_return_pct'] > 15.0:
                        print(f"    [GOOD] Meets good hedge fund standards")
                        profitable_combinations.append(result)
                    elif metrics['max_drawdown_pct'] <= 15.0 and metrics['total_return_pct'] > 20.0:
                        print(f"    [EXCELLENT] Meets excellent hedge fund standards")
                        profitable_combinations.append(result)
                    else:
                        print(f"    [FAIL] Does not meet hedge fund standards")
        
        except Exception as e:
            print(f"  [!] Error: {e}")
            continue
    
    print(f"\n{'='*80}")
    print("NEW INSTRUMENTS OPTIMIZATION SUMMARY")
    print(f"{'='*80}")
    
    print(f"\n{'Instrument':<12} {'Strategy':<20} {'Return %':<12} {'Drawdown %':<12} {'Status':<15}")
    print("-" * 71)
    
    for result in all_results:
        metrics = result['metrics']
        if metrics['max_drawdown_pct'] <= 15.0 and metrics['total_return_pct'] > 20.0:
            status = 'EXCELLENT'
        elif metrics['max_drawdown_pct'] <= 20.0 and metrics['total_return_pct'] > 15.0:
            status = 'GOOD'
        elif metrics['max_drawdown_pct'] <= 25.0 and metrics['total_return_pct'] > 10.0:
            status = 'ACCEPTABLE'
        else:
            status = 'FAIL'
        
        print(f"{result['instrument']:<12} {result['strategy']:<20} {metrics['total_return_pct']:<12.2f} {metrics['max_drawdown_pct']:<12.2f} {status:<15}")
    
    print(f"\n{'='*60}")
    print(f"PROFITABLE COMBINATIONS: {len(profitable_combinations)}")
    print(f"{'='*60}")
    
    if profitable_combinations:
        for result in profitable_combinations:
            print(f"  {result['instrument']} - {result['strategy']}: {result['metrics']['total_return_pct']:.2f}% return, {result['metrics']['max_drawdown_pct']:.2f}% drawdown")
        
        avg_return = np.mean([r['metrics']['total_return_pct'] for r in profitable_combinations])
        avg_drawdown = np.mean([r['metrics']['max_drawdown_pct'] for r in profitable_combinations])
        
        print(f"\nNew Instruments Portfolio Level (Average):")
        print(f"  Avg Return: {avg_return:.2f}%")
        print(f"  Avg Drawdown: {avg_drawdown:.2f}%")
        print(f"  Return/DD: {avg_return / avg_drawdown if avg_drawdown > 0 else 0:.2f}")
    else:
        print(f"\n[WARNING] No profitable combinations found for new instruments")
    
    output_file = RESULTS_DIR / f'new_instruments_optimizer_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    
    final_results = {
        'new_instruments': NEW_INSTRUMENTS,
        'strategies_tested': STRATEGIES_TO_TEST,
        'profitable_combinations': profitable_combinations,
        'all_results': all_results,
    }
    
    with open(output_file, 'w') as f:
        json.dump(final_results, f, indent=2)
    
    print(f"\n{'='*80}")
    print(f"Results saved to: {output_file}")
    print(f"{'='*80}")
    
    return final_results


if __name__ == '__main__':
    run_new_instruments_optimization()
