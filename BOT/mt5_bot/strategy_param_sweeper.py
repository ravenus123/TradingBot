"""Parameter sweeper for new strategies (breakout, mean_reversion, trend_momentum).
Runs short validations to surface promising parameter combos and saves results.
"""
# --- path bootstrap (allow running as a script: add BOT/ to sys.path) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import json
from pathlib import Path
from itertools import product

from mt5_bot.strategy_eval import simulate_strategy
from mt5_bot.trend_momentum import generate_trend_momentum_signal
from mt5_bot.mean_reversion import generate_mean_reversion_signal
from mt5_bot.breakout import generate_breakout_signal


def sweep_breakout(symbol: str):
    grid = {
        'squeeze_thresh': [0.0003, 0.0006, 0.001],
        'look': [6, 8, 12],
        'buffer_pct': [0.03, 0.05, 0.08],
    }
    picks = []
    for st, lk, buf in product(grid['squeeze_thresh'], grid['look'], grid['buffer_pct']):
        params = {'squeeze_thresh': st, 'look': lk, 'buffer_pct': buf}
        res = simulate_strategy(generate_breakout_signal, symbol, periods=12, period_bars=1200, bars=20000, params=params)
        picks.append({'params': params, 'metrics': res})
    picks.sort(key=lambda x: x['metrics'].get('avg_return_pct', -999), reverse=True)
    return picks


def sweep_meanrev(symbol: str):
    grid = {'window': [10, 20, 30], 'z_threshold': [1.5, 2.0, 2.5], 'stop_atr_mult': [0.8, 1.0, 1.2]}
    picks = []
    for w, zt, sam in product(grid['window'], grid['z_threshold'], grid['stop_atr_mult']):
        params = {'window': w, 'z_threshold': zt, 'stop_atr_mult': sam}
        res = simulate_strategy(generate_mean_reversion_signal, symbol, periods=12, period_bars=1200, bars=20000, params=params)
        picks.append({'params': params, 'metrics': res})
    picks.sort(key=lambda x: x['metrics'].get('avg_return_pct', -999), reverse=True)
    return picks


def sweep_trend(symbol: str):
    grid = {'h1_ema_period': [34, 50, 100], 'm5_ema_period': [8, 12, 20], 'stop_atr_mult': [1.0, 1.5]}
    picks = []
    for h1, m5, sam in product(grid['h1_ema_period'], grid['m5_ema_period'], grid['stop_atr_mult']):
        params = {'h1_ema_period': h1, 'm5_ema_period': m5, 'stop_atr_mult': sam}
        res = simulate_strategy(generate_trend_momentum_signal, symbol, periods=12, period_bars=1200, bars=20000, params=params)
        picks.append({'params': params, 'metrics': res})
    picks.sort(key=lambda x: x['metrics'].get('avg_return_pct', -999), reverse=True)
    return picks


def main():
    symbols = ['EURUSD', 'NAS100', 'XAUUSD']
    out = {}
    for s in symbols:
        print('Sweeping breakout for', s)
        out.setdefault(s, {})['breakout'] = sweep_breakout(s)[:5]
        print('Sweeping mean-reversion for', s)
        out.setdefault(s, {})['mean_reversion'] = sweep_meanrev(s)[:5]
        print('Sweeping trend-momentum for', s)
        out.setdefault(s, {})['trend_momentum'] = sweep_trend(s)[:5]

    p = Path(__file__).parent / 'liverun' / 'strategy_param_sweep.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2))
    print('Saved sweep results to', p)


if __name__ == '__main__':
    main()
