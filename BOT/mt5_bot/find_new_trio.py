"""
Find 2 new instruments to complete the trio with EURUSD
Test: GBPUSD, USDJPY, US30, US100, GER30, UK100
Keep EURUSD as anchor, find best 2 generalizers
"""
# --- path bootstrap (allow running as a script: add BOT/ to sys.path) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backtest_improved import run_live_smc_engine_backtest, fetch_data
from smart_money_strategy import SYMBOL_RULES

# Candidate instruments with configs
candidates = {
    'GBPUSD': {
        'rr': 1.8,
        'min_score': 3,
        'atr_mult_stop': 0.75,
        'min_sweep_atr': 0.03,
        'max_spread_pips': 3.0,
        'trail_mult': None,
        'use_ob': False,
        'sessions': [(6, 18)],
        'loose_bias': True,
        'no_partial': True,
        'timeout_bars': 192,
        'contrarian': True,
        'momentum_bars': 2,
        'pullback_pct': 0.382,
    },
    'USDJPY': {
        'rr': 1.5,
        'min_score': 3,
        'atr_mult_stop': 0.70,
        'min_sweep_atr': 0.025,
        'max_spread_pips': 2.5,
        'trail_mult': None,
        'use_ob': False,
        'sessions': [(6, 18)],
        'loose_bias': True,
        'no_partial': True,
        'timeout_bars': 192,
        'contrarian': False,
        'momentum_bars': 2,
        'pullback_pct': 0.382,
    },
    'US30': {  # Dow Jones
        'rr': 1.8,
        'min_score': 4,
        'atr_mult_stop': 0.60,
        'min_sweep_atr': 0.015,
        'max_spread_pips': 6.0,
        'trail_mult': 0.8,
        'use_ob': True,
        'sessions': [(12, 23)],
        'no_partial': True,
        'contrarian': True,
        'momentum_bars': 2,
        'pullback_pct': 0.382,
    },
    'US100': {  # NASDAQ alternative naming
        'rr': 1.8,
        'min_score': 4,
        'atr_mult_stop': 0.55,
        'min_sweep_atr': 0.02,
        'max_spread_pips': 8.0,
        'trail_mult': 0.8,
        'use_ob': True,
        'sessions': [(12, 23)],
        'no_partial': True,
        'contrarian': True,
        'momentum_bars': 2,
        'pullback_pct': 0.382,
    },
    'GER30': {  # DAX
        'rr': 1.8,
        'min_score': 4,
        'atr_mult_stop': 0.60,
        'min_sweep_atr': 0.02,
        'max_spread_pips': 6.0,
        'trail_mult': 0.8,
        'use_ob': True,
        'sessions': [(7, 16)],  # German market hours
        'no_partial': True,
        'contrarian': False,
        'momentum_bars': 2,
        'pullback_pct': 0.382,
    },
}


def test_instrument(symbol: str, rules: dict, total_bars: int = 30000):
    """Quick 60/20/20 split test for a candidate"""
    
    print(f"\n{'='*70}")
    print(f"TESTING: {symbol}")
    print(f"{'='*70}")
    
    # Temporarily add to SYMBOL_RULES for backtest
    SYMBOL_RULES[symbol] = rules
    
    df = fetch_data(symbol, bars=total_bars)
    if df is None or len(df) < total_bars * 0.9:
        print(f"❌ {symbol}: Could not fetch data")
        return None
    
    bars_60 = int(total_bars * 0.6)
    bars_20 = int(total_bars * 0.2)
    
    splits = [
        ('in_sample', 0, bars_60),
        ('validation', bars_60, bars_60 + bars_20),
        ('untouched', bars_60 + bars_20, total_bars)
    ]
    
    results = {}
    for split_name, start, end in splits:
        df_split = df.iloc[start:end].copy()
        result = run_live_smc_engine_backtest(df_split, symbol, risk_pct=1.0)
        
        if result and 'metrics' in result:
            metrics = result['metrics']
            results[split_name] = {
                'return_pct': metrics.get('return_pct', 0),
                'win_rate': metrics.get('win_rate', 0),
                'total_trades': metrics.get('total_trades', 0),
                'profit_factor': metrics.get('profit_factor', 0),
            }
            print(f"  {split_name:12}: {metrics.get('return_pct', 0):6.2f}% | WR: {metrics.get('win_rate', 0):5.1f}% | Trades: {metrics.get('total_trades', 0):3}")
        else:
            results[split_name] = None
            print(f"  {split_name:12}: ERROR")
    
    # Score generalization
    if all(results.get(s) for s in ['in_sample', 'validation', 'untouched']):
        in_ret = results['in_sample']['return_pct']
        val_ret = results['validation']['return_pct']
        unt_ret = results['untouched']['return_pct']
        
        # Scoring: positive on all splits = good
        all_positive = in_ret > 0 and val_ret > 0 and unt_ret > 0
        min_return = min(in_ret, val_ret, unt_ret)
        avg_return = (in_ret + val_ret + unt_ret) / 3
        
        # Consistency score (lower variance = better)
        variance = ((in_ret - avg_return)**2 + (val_ret - avg_return)**2 + (unt_ret - avg_return)**2) / 3
        consistency = -variance  # Higher (less negative) = more consistent
        
        # Overall score: positive returns + consistency
        score = avg_return if all_positive else avg_return * 0.5  # Penalty if any split negative
        
        results['score'] = score
        results['all_positive'] = all_positive
        results['min_return'] = min_return
        results['avg_return'] = avg_return
        
        verdict = "✅ PASS" if all_positive and min_return > 3 else "⚠️  MAYBE" if all_positive else "❌ FAIL"
        print(f"  VERDICT: {verdict} | Min: {min_return:.2f}% | Avg: {avg_return:.2f}% | Score: {score:.1f}")
    else:
        results['score'] = -999
        print(f"  VERDICT: ❌ FAIL - Missing data")
    
    # Clean up
    if symbol in SYMBOL_RULES and symbol not in ['EURUSD', 'NAS100', 'XAUUSD']:
        del SYMBOL_RULES[symbol]
    
    return results


def main():
    print("\n" + "="*70)
    print("FINDING NEW TRIO - Testing Candidates")
    print("Anchor: EURUSD (already proven)")
    print("Need: 2 more instruments with consistent positive returns")
    print("="*70)
    
    all_results = {}
    
    for symbol, rules in candidates.items():
        all_results[symbol] = test_instrument(symbol, rules)
    
    # Rank by score
    print("\n" + "="*70)
    print("RANKING RESULTS")
    print("="*70)
    
    ranked = [(sym, data) for sym, data in all_results.items() if data and data.get('score', -999) > -900]
    ranked.sort(key=lambda x: x[1]['score'], reverse=True)
    
    print(f"\n{'Rank':<5} {'Symbol':<8} {'Min Ret':<10} {'Avg Ret':<10} {'All Pos':<8} {'Score':<8}")
    print("-" * 70)
    
    for i, (sym, data) in enumerate(ranked, 1):
        print(f"{i:<5} {sym:<8} {data['min_return']:>8.2f}% {data['avg_return']:>8.2f}% {str(data['all_positive']):<8} {data['score']:>7.1f}")
    
    # Recommend top 2
    if len(ranked) >= 2:
        top2 = [r[0] for r in ranked[:2]]
        print(f"\n{'='*70}")
        print(f"🎯 RECOMMENDED NEW TRIO:")
        print(f"   1. EURUSD (anchor)")
        print(f"   2. {top2[0]}")
        print(f"   3. {top2[1]}")
        print(f"{'='*70}")
    
    # Save results
    output_file = Path(__file__).parent / 'new_trio_results.json'
    with open(output_file, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nResults saved to: {output_file}")


if __name__ == '__main__':
    main()
