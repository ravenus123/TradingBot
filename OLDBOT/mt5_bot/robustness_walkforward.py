"""Simple walk-forward runner.

For each symbol in the slate, slide a train/test window across history and select
the best strategy on the train window (by final balance), then evaluate on test.
Saves per-split summaries into a run folder.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import math
from typing import Dict, List

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
BOT_DIR = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BOT_DIR))

from equity_curve_stepwise import load_candidates, build_stepwise_equity


def _max_drawdown_pct(series: pd.Series) -> float:
    if series is None or len(series) == 0:
        return float("nan")
    values = pd.Series(series).astype(float).values
    peak = values[0]
    worst = 0.0
    for value in values:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (peak - value) / peak * 100.0
            if drawdown > worst:
                worst = drawdown
    return float(worst)


def run_walkforward(train_days: int, test_days: int, step_days: int, candidates: List[Dict], outdir: Path, bars: int):
    outdir.mkdir(parents=True, exist_ok=True)
    results = []

    # map candidates by symbol
    by_symbol: Dict[str, List[Dict]] = {}
    for c in candidates:
        by_symbol.setdefault(c['symbol'], []).append(c)

    for symbol, cands in by_symbol.items():
        print(f"Walk-forward for {symbol} with {len(cands)} candidates")
        # fetch full data
        from backtest_improved import fetch_data as _fetch
        df = _fetch(symbol, bars=bars)
        if df is None:
            print(f"  No data for {symbol}")
            continue

        # compute available days from index (assume DatetimeIndex)
        idx = pd.to_datetime(df.index)
        if len(idx) < 10:
            continue
        start_date = idx[0]
        end_date = idx[-1]
        total_days = (end_date - start_date).days
        # convert days to M15 bars
        bars_per_day = 24 * 4
        train_bars = train_days * bars_per_day
        test_bars = test_days * bars_per_day
        step_bars = step_days * bars_per_day

        max_start = max(0, len(df) - train_bars - test_bars)
        start_positions = list(range(0, max_start + 1, max(1, step_bars)))

        for sp in start_positions:
            train_start = sp
            train_start_date = df.index[train_start]
            # evaluate each candidate on train
            best = None
            best_balance = -math.inf
            for c in cands:
                try:
                    s_train, _ = build_stepwise_equity(c, period_bars=train_bars, bars=bars, risk_pct=1.0, start_index=train_start)
                    if len(s_train) == 0:
                        continue
                    bal = float(s_train.iloc[-1])
                    dd = _max_drawdown_pct(s_train)
                    score = bal - (bal * (dd / 100.0) * 0.5)
                except Exception:
                    continue
                if score > best_balance:
                    best_balance = score
                    best = c

            if best is None:
                continue

            # test window start is train_start + train_bars
            test_start = train_start + train_bars
            try:
                s_test, _ = build_stepwise_equity(best, period_bars=test_bars, bars=bars, risk_pct=1.0, start_index=test_start)
                test_final = float(s_test.iloc[-1]) if len(s_test) else float('nan')
                test_dd = _max_drawdown_pct(s_test)
            except Exception as e:
                test_final = float('nan')
                test_dd = float('nan')

            rec = {
                'symbol': symbol,
                'train_start_idx': train_start,
                'train_start_date': str(train_start_date),
                'best_label': best['label'],
                'train_score': best_balance,
                'test_final': test_final,
                'test_drawdown_pct': test_dd,
            }
            results.append(rec)

    # save results
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / 'walkforward_summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    pd.DataFrame(results).to_csv(outdir / 'walkforward_summary.csv', index=False)
    print('Walk-forward complete, saved to', outdir)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--train-days', type=int, default=180)
    parser.add_argument('--test-days', type=int, default=90)
    parser.add_argument('--step-days', type=int, default=90)
    parser.add_argument('--bars', type=int, default=8000)
    parser.add_argument('--out', type=str, default=str(BOT_DIR / 'liverun' / 'walkforward'))
    args = parser.parse_args()

    candidates = load_candidates(limit=None)
    outdir = Path(args.out) / f'run_{int(time.time())}'
    run_walkforward(args.train_days, args.test_days, args.step_days, candidates, outdir, args.bars)


if __name__ == '__main__':
    main()
