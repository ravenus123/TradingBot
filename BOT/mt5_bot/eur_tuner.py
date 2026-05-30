"""Focused tuner for EURUSD structural variants.
Tries a small grid of structural toggles (require_fvg, pullback_min, min_score)
and keeps any setting that improves average return in a short validation.
"""
import json
import itertools
from pathlib import Path

import OLDBOT.mt5_bot.smart_money_strategy as sm
import OLDBOT.mt5_bot.backtest_improved as bt

BEST_FILE = Path(__file__).parent / 'best_settings.json'


def evaluate():
    return bt.validate_live_smc_engine(symbols=('EURUSD',), periods=8, period_bars=1200, bars=20000).get('EURUSD', {})


def main():
    base = sm.SYMBOL_RULES.get('EURUSD', {}).copy()
    best_rules = base.copy()
    best_metrics = evaluate()
    print('Baseline EURUSD:', best_metrics)

    toggles = [True, False]
    pullback_vals = [0.08, 0.10, 0.12, 0.15]
    min_score_vals = [0, 1, 2]

    for require_fvg, pullback_min, min_score in itertools.product(toggles, pullback_vals, min_score_vals):
        rules = base.copy()
        rules['require_fvg'] = require_fvg
        rules['pullback_min'] = pullback_min
        rules['min_score'] = min_score
        sm.SYMBOL_RULES['EURUSD'].update(rules)
        metrics = evaluate()
        avg = metrics.get('avg_return_pct', -999)
        trades = metrics.get('avg_trades_per_period', 0)
        print(f"Try fvg={require_fvg} pullback_min={pullback_min} min_score={min_score} -> avg_ret={avg:+.3f} trades={trades}")
        # Accept if average return improves
        if avg > best_metrics.get('avg_return_pct', -999):
            best_metrics = metrics
            best_rules = rules.copy()

    print('\nBest EURUSD rules found:', best_rules, best_metrics)

    # persist into best_settings.json
    try:
        data = json.loads(BEST_FILE.read_text()) if BEST_FILE.exists() else {}
    except Exception:
        data = {}
    data = data or {}
    data['EURUSD'] = data.get('EURUSD', {})
    for k in ['rr', 'atr_mult_stop', 'trail_mult', 'min_score', 'pullback_pct', 'require_fvg', 'no_partial', 'sweep_lookback', 'sweep_search', 'pullback_min']:
        if k in best_rules:
            data['EURUSD'][k] = best_rules[k]
    BEST_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
    print('Saved EURUSD picks to', BEST_FILE)


if __name__ == '__main__':
    main()
