"""Fast hill-climb optimizer for quick live-style validation.
This script runs short `validate_live_smc_engine` jobs and tries small
parameter perturbations to quickly find improved settings for the
three core symbols (EURUSD, NAS100, XAUUSD).

Usage: run from repo root: `python -m mt5_bot.fast_optimizer` or
`python mt5_bot/fast_optimizer.py`.
"""
# --- path bootstrap (allow running as a script: add BOT/ to sys.path) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import json
import random
import copy
from pathlib import Path

import mt5_bot.smart_money_strategy as sm
import mt5_bot.backtest_improved as bt

BEST_FILE = Path(__file__).parent / 'best_settings.json'


def evaluate(symbol, periods=3, period_bars=800, bars=15000):
    # Run a very short validation to get quick feedback
    res = bt.validate_live_smc_engine(symbols=(symbol,), periods=periods, period_bars=period_bars, bars=bars)
    return res.get(symbol, {})


def try_tweaks(symbol, base_rules, iterations=20):
    best_rules = copy.deepcopy(base_rules)
    best_metrics = evaluate(symbol)
    print(f"Baseline {symbol}: {best_metrics}")

    for i in range(iterations):
        rules = copy.deepcopy(best_rules)
        # Random small perturbations on numeric params
        for key in ['rr', 'atr_mult_stop', 'trail_mult', 'pullback_pct', 'min_score']:
            if key in rules:
                val = rules[key]
                if isinstance(val, bool):
                    continue
                # scale perturbation based on value
                scale = max(0.05, abs(val) * 0.12)
                rules[key] = max(0, round(val + random.uniform(-scale, scale), 4))

        # discrete tweaks
        if 'sweep_lookback' in rules:
            rules['sweep_lookback'] = max(4, int(rules['sweep_lookback'] + random.randint(-4, 4)))
        if 'sweep_search' in rules:
            rules['sweep_search'] = max(8, int(rules['sweep_search'] + random.randint(-8, 8)))

        # occasionally toggle require_fvg / no_partial for exploration
        if random.random() < 0.25:
            rules['require_fvg'] = not bool(rules.get('require_fvg', False))
        if random.random() < 0.25:
            rules['no_partial'] = not bool(rules.get('no_partial', False))

        # Apply rules in-memory and evaluate
        sm.SYMBOL_RULES[symbol].update(rules)
        metrics = evaluate(symbol)
        avg_ret = metrics.get('avg_return_pct', -999)
        trades = metrics.get('avg_trades_per_period', 0)

        print(f"Iter {i+1}/{iterations} {symbol}: ret={avg_ret:+.3f} trades={trades:.2f} tweaks={ {k:rules[k] for k in rules if k in ['rr','atr_mult_stop','trail_mult','pullback_pct','min_score','require_fvg','no_partial']} }")

        # Accept if average return improves or trades increased significantly with similar return
        best_ret = best_metrics.get('avg_return_pct', -999)
        if avg_ret > best_ret or (avg_ret >= best_ret - 0.01 and trades > best_metrics.get('avg_trades_per_period', 0) + 1):
            best_metrics = metrics
            best_rules = copy.deepcopy(rules)

    # Restore best rules to global and return
    sm.SYMBOL_RULES[symbol].update(best_rules)
    return best_rules, best_metrics


def save_picks(picks: dict):
    if BEST_FILE.exists():
        try:
            with open(BEST_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}
    else:
        data = {}

    for sym, rules in picks.items():
        data[sym] = data.get(sym, {})
        # persist only a handful of keys for live adapter compatibility
        for k in ['rr', 'atr_mult_stop', 'trail_mult', 'min_score', 'pullback_pct', 'require_fvg', 'no_partial', 'sweep_lookback', 'sweep_search']:
            if k in rules:
                data[sym][k] = rules[k]

    with open(BEST_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    print(f"Saved picks to {BEST_FILE}")


def main():
    symbols = ['NAS100', 'XAUUSD', 'EURUSD']
    picks = {}
    metrics = {}

    # Quick focused search for NAS100 and XAUUSD (more iterations)
    for sym in ['NAS100', 'XAUUSD']:
        base = sm.SYMBOL_RULES.get(sym, {})
        r, m = try_tweaks(sym, base, iterations=18)
        picks[sym] = r
        metrics[sym] = m

    # For EURUSD, try structural toggles first then small tweaks
    eur_base = sm.SYMBOL_RULES.get('EURUSD', {})
    eur_try = copy.deepcopy(eur_base)
    eur_try['require_fvg'] = True
    eur_try['min_score'] = max(1, int(eur_try.get('min_score', 0) + 1))
    eur_try['no_partial'] = True
    eur_try['pullback_min'] = max(0.12, eur_try.get('pullback_min', 0.08))
    sm.SYMBOL_RULES['EURUSD'].update(eur_try)
    # Evaluate structural change
    eur_metrics = evaluate('EURUSD')
    print(f"EURUSD structural try: {eur_metrics}")
    # If structural try promising, run tweaks
    if eur_metrics.get('avg_return_pct', -999) > -0.5:
        r, m = try_tweaks('EURUSD', sm.SYMBOL_RULES['EURUSD'], iterations=20)
        picks['EURUSD'] = r
        metrics['EURUSD'] = m
    else:
        picks['EURUSD'] = eur_try
        metrics['EURUSD'] = eur_metrics

    print('\nFinal picks:')
    for s in symbols:
        print(s, picks.get(s), metrics.get(s))

    save_picks(picks)


if __name__ == '__main__':
    main()
