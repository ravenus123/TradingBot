"""Quick evaluator for arbitrary strategy generators.
Runs short randomized window simulations and reports avg return_pct, avg trades, win rate.
"""
import random
import json
from pathlib import Path
from typing import Callable

import pandas as pd
import numpy as np

from OLDBOT.mt5_bot.backtest_improved import fetch_data


def _resample_ohlc_for_live_engine(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    work = df.copy()
    if 'Time' in work.columns and not isinstance(work.index, pd.DatetimeIndex):
        work = work.set_index(pd.to_datetime(work['Time']))
    return work.resample(rule).agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna()


def simulate_strategy(generator: Callable, symbol: str, periods: int = 20, period_bars: int = 1200, bars: int = 25000, risk_pct: float = 1.0, params: dict | None = None):
    df = fetch_data(symbol, bars=bars)
    if df is None or len(df) < period_bars + 50:
        return {}

    returns = []
    trades_count = []
    wins = 0

    for _ in range(periods):
        start = random.randint(0, len(df) - period_bars - 1)
        window = df.iloc[start:start+period_bars].copy()
        df_h1 = _resample_ohlc_for_live_engine(window, '1h')
        df_m5 = _resample_ohlc_for_live_engine(window, '5min')

        # call generator (pass params if supported)
        try:
            sig = generator(df_h1, df_m5, symbol, {}, params) if params is not None else generator(df_h1, df_m5, symbol, {})
        except Exception:
            try:
                sig = generator(df_h1, df_m5, symbol, {})
            except Exception:
                sig = None
        if not sig:
            returns.append(0.0)
            trades_count.append(0)
            continue

        # simulate: entry at last close; find exit in future bars
        entry_price = float(sig.get('entry', df_m5['Close'].iloc[-1]))
        stop = float(sig.get('stop', entry_price - 0.01))
        tp = float(sig.get('tp', entry_price + 0.02))
        direction = sig.get('direction', 'BUY')

        # scan forward after entry within window
        out = None
        m5_price = df_m5['Close'].astype(float).values
        highs = df_m5['High'].astype(float).values
        lows = df_m5['Low'].astype(float).values
        for i in range(len(m5_price)):
            h = highs[i]; l = lows[i]
            if direction == 'BUY':
                if h >= tp:
                    out = tp; break
                if l <= stop:
                    out = stop; break
            else:
                if l <= tp:
                    out = tp; break
                if h >= stop:
                    out = stop; break

        if out is None:
            # mark-to-market at last close
            out = float(m5_price[-1])

        # compute R multiple
        if direction == 'BUY':
            risk_unit = entry_price - stop if entry_price - stop != 0 else 1e-6
            r = (out - entry_price) / risk_unit
        else:
            risk_unit = stop - entry_price if stop - entry_price != 0 else 1e-6
            r = (entry_price - out) / risk_unit

        # convert to pct return relative to risk_pct
        return_pct = r * float(risk_pct)
        returns.append(return_pct)
        trades_count.append(1)
        if r > 0:
            wins += 1

    summary = {
        'profitable_pct': float(np.mean(np.array(returns) > 0) * 100),
        'avg_return_pct': float(np.mean(returns) * 100),
        'median_return_pct': float(np.median(returns) * 100),
        'avg_trades_per_period': float(np.mean(trades_count)),
        'win_rate_pct': float(wins / periods * 100),
    }
    return summary


def main():
    from OLDBOT.mt5_bot.trend_momentum import generate_trend_momentum_signal
    from OLDBOT.mt5_bot.mean_reversion import generate_mean_reversion_signal
    from OLDBOT.mt5_bot.breakout import generate_breakout_signal

    symbols = ['EURUSD', 'NAS100', 'XAUUSD']
    strategies = [
        ('trend_momentum_v1', generate_trend_momentum_signal),
        ('mean_reversion_v1', generate_mean_reversion_signal),
        ('breakout_v1', generate_breakout_signal),
    ]

    results = {}
    for name, fn in strategies:
        results[name] = {}
        for s in symbols:
            print(f"Evaluating {name} on {s} ...")
            res = simulate_strategy(fn, s, periods=20, period_bars=1200, bars=20000, risk_pct=1.0)
            results[name][s] = res

    out = Path(__file__).parent / 'liverun' / 'strategy_eval_summary.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print('Saved summary to', out)


if __name__ == '__main__':
    main()
