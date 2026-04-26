#!/usr/bin/env python3
"""Clean robustness test runner - no PowerShell BS"""

from mt5_bot.backtest_improved import portfolio_calendar_walk, monte_carlo_live_smc_engine

print("="*80)
print("PORTFOLIO CALENDAR WALK - 8 WINDOWS")
print("="*80)

result = portfolio_calendar_walk(
    symbols=['EURUSD', 'NAS100', 'XAUUSD'], 
    windows=8, 
    window_bars=1900
)

s = result['summary']
print("\n" + "="*80)
print("RESULTS:")
print("="*80)
print(f"Windows Tested:     {s['windows']}")
print(f"Profitable:         {s['profitable_pct']:.1f}%")
print(f"Mean Return:        {s['mean_return_pct']:.2f}%")
print(f"Median Return:      {s['median_return_pct']:.2f}%")
print(f"Worst Return:       {s['worst_return_pct']:.2f}%")
print(f"Best Return:        {s['best_return_pct']:.2f}%")
print(f"Mean Max DD:        {s['mean_max_dd_pct']:.2f}%")
print(f"Worst Max DD:       {s['worst_max_dd_pct']:.2f}%")
print("="*80)

# Show per-window details
print("\nPER-WINDOW BREAKDOWN:")
for w in result['per_window']:
    print(f"  {w['start'][:10]} to {w['end'][:10]} | Return: {w['return_pct']:+6.2f}% | Trades: {w['trades']:2d} | Max DD: {w['max_dd_pct']:.2f}%")

print("\n" + "="*80)
print("MONTE CARLO ROBUSTNESS (100 simulations)")
print("="*80)

mc_result = monte_carlo_live_smc_engine(
    symbols=['EURUSD', 'NAS100', 'XAUUSD'],
    period_bars=4000,
    bars=20000,
    simulations=100
)

for sym, data in mc_result.items():
    print(f"\n{sym}:")
    print(f"  Trades: {data['trades']}")
    print(f"  Profitable: {data['profitable_pct']:.1f}%")
    print(f"  Mean Return: {data['mean_return_pct']:.2f}%")
    print(f"  P5 (worst): {data['p5_return_pct']:.2f}%")
    print(f"  P95 (best): {data['p95_return_pct']:.2f}%")
    print(f"  Mean DD: {data['mean_max_dd_pct']:.2f}%")

print("\n" + "="*80)
print("TEST COMPLETE")
print("="*80)
