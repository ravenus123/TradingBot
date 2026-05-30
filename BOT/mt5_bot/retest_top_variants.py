"""Re-evaluate top variants from strategy_param_sweep.json with longer sampling.
This gives a more stable estimate before moving to full walk-forward tests.
"""
# --- path bootstrap (allow running as a script: add BOT/ to sys.path) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import json
from pathlib import Path

from mt5_bot.strategy_eval import simulate_strategy
from mt5_bot.trend_momentum import generate_trend_momentum_signal
from mt5_bot.mean_reversion import generate_mean_reversion_signal
from mt5_bot.breakout import generate_breakout_signal


STRATEGY_FN = {
    'trend_momentum': generate_trend_momentum_signal,
    'mean_reversion': generate_mean_reversion_signal,
    'breakout': generate_breakout_signal,
}


def main():
    p = Path(__file__).parent / 'liverun' / 'strategy_param_sweep.json'
    if not p.exists():
        print('No sweep file found:', p)
        return
    data = json.loads(p.read_text())
    out = {}
    for symbol, groups in data.items():
        out[symbol] = {}
        for strat, picks in groups.items():
            out[symbol][strat] = []
            fn = STRATEGY_FN.get(strat)
            if fn is None:
                continue
            for pick in picks[:5]:
                params = pick.get('params', {})
                print(f"Re-testing {strat} {symbol} params={params}")
                res = simulate_strategy(fn, symbol, periods=100, period_bars=1200, bars=25000, params=params)
                out[symbol][strat].append({'params': params, 'metrics': res})

    outp = Path(__file__).parent / 'liverun' / 'retest_top_variants.json'
    outp.write_text(json.dumps(out, indent=2))
    print('Saved retest results to', outp)


if __name__ == '__main__':
    main()
