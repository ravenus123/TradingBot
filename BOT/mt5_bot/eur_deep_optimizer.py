"""Deeper optimizer for EURUSD: grid search over key numeric params
followed by a small hill-climb. Saves best result to best_settings.json.
"""
import json
import random
import copy
from pathlib import Path

import OLDBOT.mt5_bot.smart_money_strategy as sm
import OLDBOT.mt5_bot.backtest_improved as bt

BEST_FILE = Path(__file__).parent / 'best_settings.json'


def evaluate(periods=20, period_bars=1200, bars=25000):
    return bt.validate_live_smc_engine(symbols=('EURUSD',), periods=periods, period_bars=period_bars, bars=bars).get('EURUSD', {})


def hill_climb(base_rules, iterations=30):
    best_rules = copy.deepcopy(base_rules)
    best_metrics = evaluate()
    for i in range(iterations):
        rules = copy.deepcopy(best_rules)
        for k in ['rr', 'atr_mult_stop', 'trail_mult', 'pullback_pct', 'min_score']:
            if k in rules and not isinstance(rules[k], bool):
                scale = max(0.02, abs(rules[k]) * 0.10)
                rules[k] = max(0, round(rules[k] + random.uniform(-scale, scale), 4))
        # toggle require_fvg occasionally
        if random.random() < 0.2:
            rules['require_fvg'] = not bool(rules.get('require_fvg', False))
        sm.SYMBOL_RULES['EURUSD'].update(rules)
        metrics = evaluate()
        avg = metrics.get('avg_return_pct', -999)
        if avg > best_metrics.get('avg_return_pct', -999):
            best_metrics = metrics
            best_rules = copy.deepcopy(rules)
            print(f"Hill climb improved: iter {i+1} avg_ret={avg:+.3f}")
    return best_rules, best_metrics


def main():
    base = sm.SYMBOL_RULES.get('EURUSD', {}).copy()
    print('Starting deep optimizer baseline evaluate...')
    baseline = evaluate()
    print('Baseline:', baseline)

    # Coarse grid
    rr_vals = [1.0, 1.25, 1.5, 1.75, 2.0]
    atr_vals = [0.8, 1.0, 1.2, 1.5]
    trail_vals = [1.2, 1.5, 1.8, 2.0]
    pullback_vals = [0.06, 0.08, 0.1, 0.12, 0.15]
    min_score_vals = [0, 1, 2]
    require_fvg_opts = [True, False]

    best_rules = base.copy()
    best_metrics = baseline

    for rr in rr_vals:
        for atr in atr_vals:
            for trail in trail_vals:
                for pb in pullback_vals:
                    for ms in min_score_vals:
                        for rf in require_fvg_opts:
                            rules = base.copy()
                            rules.update({'rr': rr, 'atr_mult_stop': atr, 'trail_mult': trail, 'pullback_pct': pb, 'min_score': ms, 'require_fvg': rf})
                            sm.SYMBOL_RULES['EURUSD'].update(rules)
                            metrics = evaluate(periods=8, period_bars=1200, bars=20000)
                            avg = metrics.get('avg_return_pct', -999)
                            trades = metrics.get('avg_trades_per_period', 0)
                            # require at least 4 trades per period on average to keep frequency
                            if avg > best_metrics.get('avg_return_pct', -999) and trades >= 3:
                                best_metrics = metrics
                                best_rules = copy.deepcopy(rules)
                                print(f"Grid improved rr={rr} atr={atr} trail={trail} pb={pb} ms={ms} rf={rf} -> avg={avg:+.3f} trades={trades}")

    # Hill-climb from best found
    sm.SYMBOL_RULES['EURUSD'].update(best_rules)
    hc_rules, hc_metrics = hill_climb(best_rules, iterations=30)
    # choose best of grid vs hill-climb
    final_rules = hc_rules if hc_metrics.get('avg_return_pct', -999) > best_metrics.get('avg_return_pct', -999) else best_rules
    final_metrics = hc_metrics if hc_metrics.get('avg_return_pct', -999) > best_metrics.get('avg_return_pct', -999) else best_metrics

    print('\nFinal EURUSD rules:', final_rules)
    print('Final metrics:', final_metrics)

    # persist
    try:
        data = json.loads(BEST_FILE.read_text()) if BEST_FILE.exists() else {}
    except Exception:
        data = {}
    data = data or {}
    data['EURUSD'] = data.get('EURUSD', {})
    for k in ['rr', 'atr_mult_stop', 'trail_mult', 'min_score', 'pullback_pct', 'require_fvg', 'no_partial', 'sweep_lookback', 'sweep_search', 'pullback_min']:
        if k in final_rules:
            data['EURUSD'][k] = final_rules[k]
    BEST_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
    print('Saved EURUSD picks to', BEST_FILE)


if __name__ == '__main__':
    main()
