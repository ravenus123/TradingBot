#!/usr/bin/env python3
"""Test bot on multiple random time periods for stability verification"""

from mt5_bot.backtest_improved import run_live_smc_engine_backtest, fetch_data
from datetime import datetime, timedelta
import random

random.seed(42)  # Reproducible

symbols = ['EURUSD', 'NAS100', 'XAUUSD']

# Generate random periods: 14-day and 30-day windows
def random_periods(bars_total=25000, num_periods=6):
    """Generate random start points for testing"""
    periods = []
    bar_14d = 14 * 24 * 4  # 14 days in M15 bars
    bar_30d = 30 * 24 * 4  # 30 days in M15 bars
    
    for _ in range(num_periods):
        # Random 14-day period
        start_14 = random.randint(bar_14d, bars_total - bar_14d)
        periods.append(('14d', start_14, start_14 + bar_14d))
        
        # Random 30-day period  
        start_30 = random.randint(bar_30d, bars_total - bar_30d)
        periods.append(('30d', start_30, start_30 + bar_30d))
    
    return periods

print("="*80)
print("MULTI-TIME PERIOD ROBUSTNESS TEST")
print("="*80)
print("\nTesting on 6 random 14-day periods + 6 random 30-day periods")
print("Fetching data...")

# Fetch once, slice later
all_data = {}
for sym in symbols:
    df = fetch_data(sym, 25000)
    if df is not None:
        all_data[sym] = df
        print(f"  {sym}: {len(df)} bars")
    else:
        print(f"  {sym}: FAILED")

periods = random_periods()

print("\n" + "="*80)
print("RUNNING TESTS")
print("="*80)

results = {sym: [] for sym in symbols}

for period_type, start_idx, end_idx in periods:
    print(f"\n--- Testing {period_type} window (bars {start_idx}-{end_idx}) ---")
    
    for sym in symbols:
        if sym not in all_data:
            continue
            
        df = all_data[sym].iloc[start_idx:end_idx].copy()
        result = run_live_smc_engine_backtest(df, sym, 1.0)
        m = result['metrics']
        
        results[sym].append({
            'period': period_type,
            'return': m['return_pct'],
            'trades': m['total_trades'],
            'dd': m.get('max_drawdown_pct', m.get('max_drawdown', 0)),
            'winrate': m['win_rate']
        })
        
        dd_val = m.get('max_drawdown_pct', m.get('max_drawdown', 0))
        print(f"  {sym}: Return {m['return_pct']:+.2f}% | Trades {m['total_trades']} | DD {dd_val:.2f}% | WR {m['win_rate']:.0f}%")

print("\n" + "="*80)
print("SUMMARY BY SYMBOL")
print("="*80)

for sym in symbols:
    if not results[sym]:
        continue
        
    returns = [r['return'] for r in results[sym]]
    trades = [r['trades'] for r in results[sym]]
    dds = [r['dd'] for r in results[sym]]
    winrates = [r['winrate'] for r in results[sym]]
    
    profitable = sum(1 for r in returns if r > 0)
    total = len(returns)
    
    print(f"\n{sym}:")
    print(f"  Periods tested: {total}")
    print(f"  Profitable: {profitable}/{total} ({100*profitable/total:.0f}%)")
    print(f"  Mean return: {sum(returns)/len(returns):.2f}%")
    print(f"  Best return: {max(returns):.2f}%")
    print(f"  Worst return: {min(returns):.2f}%")
    print(f"  Mean trades: {sum(trades)/len(trades):.1f}")
    print(f"  Mean DD: {sum(dds)/len(dds):.2f}%")
    print(f"  Worst DD: {max(dds):.2f}%")
    print(f"  Mean winrate: {sum(winrates)/len(winrates):.0f}%")

print("\n" + "="*80)
print("TEST COMPLETE")
print("="*80)
