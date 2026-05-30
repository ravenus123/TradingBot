"""Generate mutated strategy variants for many instruments, validate them quickly,
and persist the top candidates to best_settings.json for later portfolio tests.

Usage: run from repo root with PYTHONPATH set. This performs short in-sample
validations (fast) to surface promising parameter variants.
"""
import json
import random
import copy
from pathlib import Path
from typing import List

import OLDBOT.mt5_bot.smart_money_strategy as sm
import OLDBOT.mt5_bot.backtest_improved as bt

BEST_FILE = Path(__file__).parent / 'best_settings.json'


def mutate_rules(base: dict) -> dict:
    r = copy.deepcopy(base)
    # numeric perturbations
    for k in ['rr', 'atr_mult_stop', 'trail_mult', 'pullback_pct', 'min_score', 'pullback_min']:
        if k in r and not isinstance(r[k], bool):
            val = float(r[k])
            scale = max(0.03, abs(val) * 0.12)
            r[k] = round(max(0.0, val + random.uniform(-scale, scale)), 4)
    # discrete toggles
    if 'require_fvg' in r:
        if random.random() < 0.25:
            r['require_fvg'] = not bool(r.get('require_fvg', False))
    # randomize sweep sizes
    if 'sweep_lookback' in r:
        r['sweep_lookback'] = max(4, int(r.get('sweep_lookback', 12) + random.randint(-6, 6)))
    if 'sweep_search' in r:
        r['sweep_search'] = max(8, int(r.get('sweep_search', 40) + random.randint(-12, 12)))
    return r


def evaluate_variant(symbol: str, rules: dict, periods=12, period_bars=1200, bars=20000) -> dict:
    # apply rules in-memory
    sm.SYMBOL_RULES[symbol].update(rules)
    res = bt.validate_live_smc_engine(symbols=(symbol,), periods=periods, period_bars=period_bars, bars=bars)
    return res.get(symbol, {})


def sweep(symbol: str, base: dict, variants: int = 20) -> List[dict]:
    picks = []
    for i in range(variants):
        v = mutate_rules(base)
        metrics = evaluate_variant(symbol, v, periods=12, period_bars=1200, bars=20000)
        picks.append({'rules': v, 'metrics': metrics})
        print(f"{symbol} variant {i+1}/{variants} -> avg_ret={metrics.get('avg_return_pct')}, trades={metrics.get('avg_trades_per_period')}")
    # sort by avg_return_pct desc
    picks = [p for p in picks if p['metrics']]
    picks.sort(key=lambda x: x['metrics'].get('avg_return_pct', -999), reverse=True)
    return picks


def save_top(symbol: str, picks: List[dict], top_k: int = 3):
    try:
        data = json.loads(BEST_FILE.read_text()) if BEST_FILE.exists() else {}
    except Exception:
        data = {}
    data = data or {}
    data['instruments'] = data.get('instruments', {})
    # persist the top k variants under instrument key as list
    data['instruments'][symbol] = data['instruments'].get(symbol, {})
    top = picks[:top_k]
    # store only a subset of keys
    stored = []
    for p in top:
        rules = p['rules']
        brief = {k: rules[k] for k in rules if k in ['rr', 'atr_mult_stop', 'trail_mult', 'min_score', 'pullback_pct', 'require_fvg', 'no_partial', 'sweep_lookback', 'sweep_search']}
        stored.append({'rules': brief, 'metrics': p['metrics']})
    data['instruments'][symbol]['variants'] = stored
    BEST_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
    print(f"Saved top {len(stored)} variants for {symbol} to {BEST_FILE}")


def main():
    instruments = ['EURUSD', 'XAUUSD', 'NAS100', 'GBPUSD', 'USDJPY', 'GBPJPY', 'BTCUSD']
    variants_per = 18
    top_k = 3

    for symbol in instruments:
        base = sm.SYMBOL_RULES.get(symbol, {})
        if not base:
            print(f"No base rules for {symbol}, skipping")
            continue
        print(f"Sweeping {symbol} with {variants_per} variants")
        picks = sweep(symbol, base, variants=variants_per)
        if picks:
            save_top(symbol, picks, top_k=top_k)

    print('Variant sweep complete.')


if __name__ == '__main__':
    main()
