#!/usr/bin/env python3
"""Comprehensive stress test - Monte Carlo + Multi-year + Random slices"""

import sys
sys.path.insert(0, 'd:\\RAVBOTGITHUB\\ULTIMA2.0\\TradingBot')

from mt5_bot.backtest_improved import monte_carlo_live_smc_engine, run_live_smc_engine_backtest, fetch_data
import random
import numpy as np

random.seed(2024)
np.random.seed(2024)

symbols = ['EURUSD', 'NAS100', 'XAUUSD']

print("="*80)
print("COMPREHENSIVE STRESS TEST")
print("="*80)

# 1. MONTE CARLO (Bootstrap) - 300 simulations
print("\n[1/3] MONTE CARLO ROBUSTNESS (300 simulations)...")
print("="*80)

mc_result = monte_carlo_live_smc_engine(
    symbols=symbols,
    period_bars=5000,
    bars=25000,
    simulations=300
)

print("\n" + "="*80)
print("MONTE CARLO RESULTS (300 sims):")
print("="*80)
for sym, data in mc_result.items():
    print(f"\n{sym}:")
    print(f"  Profitable:    {data['profitable_pct']:.1f}%")
    print(f"  Mean Return:   {data['mean_return_pct']:.2f}%")
    print(f"  Median:        {data['median_return_pct']:.2f}%")
    print(f"  Worst Case:    {data['p5_return_pct']:.2f}% (P5)")
    print(f"  Best Case:     {data['p95_return_pct']:.2f}% (P95)")
    print(f"  Mean DD:       {data['mean_max_dd_pct']:.2f}%")
    print(f"  Worst DD:      {data['p95_max_dd_pct']:.2f}%")

# 2. RANDOM SLICE TEST - 20 random periods across all data
print("\n" + "="*80)
print("[2/3] RANDOM SLICE ROBUSTNESS (20 random periods)")
print("="*80)

# Fetch full history
full_data = {}
for sym in symbols:
    df = fetch_data(sym, 50000)  # ~2 years
    if df is not None:
        full_data[sym] = df
        print(f"{sym}: {len(df)} bars")

# Generate 20 random slices (30-day each)
slices = []
for i in range(20):
    for sym in symbols:
        if sym in full_data:
            max_start = len(full_data[sym]) - 30*24*4  # 30 days in M15
            if max_start > 1000:
                start = random.randint(1000, max_start)
                end = start + 30*24*4
                slices.append((sym, start, end, i))

print(f"\nTesting {len(slices)} random 30-day slices...")

slice_results = {sym: [] for sym in symbols}

for sym, start, end, idx in slices[:60]:  # Test 60 slices (20 per symbol)
    df = full_data[sym].iloc[start:end].copy()
    result = run_live_smc_engine_backtest(df, sym, 1.0)
    m = result['metrics']
    
    slice_results[sym].append({
        'return': m['return_pct'],
        'trades': m['total_trades'],
        'dd': m.get('max_drawdown_pct', 0),
        'winrate': m['win_rate']
    })

print("\nRANDOM SLICE SUMMARY:")
for sym in symbols:
    if not slice_results[sym]:
        continue
    rets = [r['return'] for r in slice_results[sym]]
    dds = [r['dd'] for r in slice_results[sym]]
    trades = [r['trades'] for r in slice_results[sym]]
    
    profitable = sum(1 for r in rets if r > 0)
    
    print(f"\n{sym} ({len(rets)} slices):")
    print(f"  Profitable:  {profitable}/{len(rets)} ({100*profitable/len(rets):.0f}%)")
    print(f"  Mean Return: {np.mean(rets):.2f}%")
    print(f"  Worst:       {min(rets):.2f}%")
    print(f"  Best:        {max(rets):.2f}%")
    print(f"  Mean DD:     {np.mean(dds):.2f}%")
    print(f"  Mean Trades: {np.mean(trades):.1f}")

# 3. YEARLY BREAKDOWN - Test 2022, 2023, 2024, 2025 separately
print("\n" + "="*80)
print("[3/3] YEARLY PERFORMANCE BREAKDOWN")
print("="*80)

years = [2022, 2023, 2024, 2025]
for year in years:
    print(f"\n--- {year} ---")
    for sym in symbols:
        if sym not in full_data:
            continue
        df = full_data[sym]
        # Filter to year
        year_df = df[df.index.year == year]
        if len(year_df) < 1000:
            print(f"  {sym}: Insufficient data ({len(year_df)} bars)")
            continue
            
        result = run_live_smc_engine_backtest(year_df, sym, 1.0)
        m = result['metrics']
        print(f"  {sym}: Return {m['return_pct']:+6.2f}% | Trades {m['total_trades']:2d} | DD {m.get('max_drawdown_pct', 0):.2f}% | WR {m['win_rate']:.0f}%")

print("\n" + "="*80)
print("STRESS TEST COMPLETE")
print("="*80)
