#!/usr/bin/env python
"""
Robustness Test - 10 Random Periods with Live SMC Engine (Fast)
Shows true performance across different market conditions
"""
from OLDBOT.mt5_bot.backtest_improved import run_live_smc_engine_backtest, fetch_data
import numpy as np

def robustness_smc_10periods(symbol, periods=10):
    """Run 10 random non-overlapping periods with SMC engine (fast version)"""
    df = fetch_data(symbol, 30000)  # Use 30K bars instead of 80K for speed
    if df is None or len(df) < 1000:
        print(f"Not enough data for {symbol}")
        return None
    
    n = len(df)
    block_size = max(160, n // periods)  # each period ~160 bars
    
    period_results = []
    combined_equity = [10000.0]
    combined_balance = 10000.0
    
    print(f"\n{'='*90}")
    print(f"{symbol} - ROBUSTNESS TEST (10 Random Periods with SMC Engine)")
    print(f"{'='*90}")
    print(f"{'Period':<8} {'Bars':<8} {'Return':<10} {'WinRate':<10} {'Trades':<8} {'PF':<8} {'DD%':<8}")
    print(f"{'-'*90}")
    
    for p in range(min(periods, max(1, n // block_size))):
        start = p * block_size
        end = min((p + 1) * block_size, n)
        
        if end - start < 100:
            continue
        
        seg = df.iloc[start:end].copy()
        result = run_live_smc_engine_backtest(seg, symbol, risk_pct=1.0)
        m = result.get('metrics', {})
        
        period_results.append(m)
        
        ret = m.get('return_pct', 0)
        wr = m.get('win_rate', 0)
        trades = m.get('total_trades', 0)
        pf = m.get('profit_factor', 0)
        dd = m.get('max_drawdown_pct', 0)
        
        print(f"  {p+1:<7} {len(seg):<8} {ret:>8.2f}% {wr:>9.1f}% {trades:>7} {pf:>7.2f} {dd:>7.1f}%")
        
        # Track combined equity
        profit = m.get('total_profit', 0)
        combined_balance += profit
    
    if period_results:
        profitable = sum(1 for m in period_results if m.get('return_pct', 0) > 0)
        avg_return = np.mean([m.get('return_pct', 0) for m in period_results])
        avg_wr = np.mean([m.get('win_rate', 0) for m in period_results])
        total_trades = sum(m.get('total_trades', 0) for m in period_results)
        avg_pf = np.mean([m.get('profit_factor', 0) for m in period_results])
        
        print(f"{'-'*90}")
        print(f"SUMMARY:")
        print(f"  Profitable Periods: {profitable}/{len(period_results)} ({100*profitable/len(period_results):.0f}%)")
        print(f"  Avg Return: {avg_return:+.2f}%")
        print(f"  Avg Win Rate: {avg_wr:.1f}%")
        print(f"  Total Trades: {total_trades}")
        print(f"  Avg PF: {avg_pf:.2f}")
        print(f"  Combined Balance: ${combined_balance:.0f} ({100*(combined_balance-10000)/10000:+.1f}%)")
        print()

# Run all 3 instruments
for sym in ['EURUSD', 'NAS100', 'XAUUSD']:
    robustness_smc_10periods(sym, periods=10)

print(f"{'='*90}")
print("ROBUSTNESS TEST COMPLETE")
print(f"{'='*90}")
