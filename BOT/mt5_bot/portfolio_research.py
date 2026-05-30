from __future__ import annotations
# --- path bootstrap (allow running as a script: add BOT/ to sys.path) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

if __package__ in (None, ''):
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from mt5_bot.db import get_db, init_trading_db
from mt5_bot.portfolio_engine import build_trade_correlation_matrix


def load_closed_trades(limit: int = 5000) -> pd.DataFrame:
    init_trading_db()
    conn = get_db()
    query = (
        "SELECT strategy_name, symbol, close_time, profit "
        "FROM trades WHERE close_time IS NOT NULL ORDER BY close_time DESC LIMIT ?"
    )
    rows = conn.execute(query, (limit,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=['strategy_name', 'symbol', 'close_time', 'profit'])
    return pd.DataFrame([dict(r) for r in rows])


def main() -> None:
    parser = argparse.ArgumentParser(description='Portfolio research utilities for strategy correlation.')
    parser.add_argument('--limit', type=int, default=5000, help='Max closed trades to load')
    parser.add_argument('--out', type=str, default='', help='Optional path to write correlation matrix JSON')
    args = parser.parse_args()

    trades = load_closed_trades(limit=args.limit)
    corr = build_trade_correlation_matrix(trades)
    if corr.empty:
        print('Not enough data to build a correlation matrix yet.')
        return

    print('Strategy correlation matrix (daily PnL):')
    print(corr.round(3).to_string())

    # Cull candidates: high absolute correlation pairs
    culls = []
    cols = list(corr.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            v = float(corr.iloc[i, j])
            if abs(v) >= 0.8:
                culls.append({'a': cols[i], 'b': cols[j], 'corr': v})

    if culls:
        print('\nHigh-correlation pairs (candidate culls):')
        for row in sorted(culls, key=lambda r: abs(r['corr']), reverse=True):
            print(f"- {row['a']} vs {row['b']}: {row['corr']:.3f}")
    else:
        print('\nNo high-correlation strategy pairs detected with current data.')

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(corr.to_dict(), indent=2), encoding='utf-8')
        print(f"Saved matrix to {out_path}")


if __name__ == '__main__':
    main()
