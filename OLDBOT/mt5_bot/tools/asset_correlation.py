import json
import sys
from pathlib import Path
import pandas as pd
import numpy as np

# ensure parent folder is on sys.path for local imports
ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest_improved import fetch_data

BOT_DIR = Path(__file__).parents[1]
CONFIG_FILE = BOT_DIR / 'liverun' / 'config' / 'production_strategy_lock.json'
OUT_DIR = BOT_DIR / 'liverun' / 'analysis'
OUT_DIR.mkdir(parents=True, exist_ok=True)

with open(CONFIG_FILE) as f:
    cfg = json.load(f)

symbols = sorted(list({s['symbol'] for s in cfg.get('strategies', [])}))
print('Symbols:', symbols)

frames = []
for sym in symbols:
    df = fetch_data(sym, bars=10000)
    if df is None:
        print(f'Failed to fetch {sym}, skipping')
        continue
    # ensure Time column exists (may be index)
    if 'Time' not in df.columns:
        df = df.reset_index()
        if 'Time' not in df.columns:
            # fallback: take first column as Time
            df = df.rename(columns={df.columns[0]: 'Time'})
    # resample daily close
    df_daily = df.resample('1D', on='Time').agg({'Close':'last'}).dropna()
    df_daily['ret'] = df_daily['Close'].pct_change()
    df_daily = df_daily[['ret']].rename(columns={'ret': sym})
    frames.append(df_daily)

if not frames:
    print('No data frames collected')
    raise SystemExit(1)

merged = pd.concat(frames, axis=1)
merged = merged.dropna(how='all')
# fill missing with 0 returns (market closed) to keep alignment
merged = merged.fillna(0.0)

corr = merged.corr()
print('Correlation matrix:\n', corr)

# find low-correlation pairs (abs < 0.3)
pairs = []
syms = corr.columns.tolist()
for i in range(len(syms)):
    for j in range(i+1, len(syms)):
        val = float(corr.iloc[i,j])
        pairs.append({'pair':(syms[i], syms[j]), 'corr': val})

low = [p for p in pairs if abs(p['corr']) < 0.3]
low_sorted = sorted(low, key=lambda x: abs(x['corr']))
print('\nLow-correlation pairs (abs < 0.3):')
for p in low_sorted:
    print(f"  {p['pair'][0]} - {p['pair'][1]}: {p['corr']:.3f}")

out = {
    'symbols': symbols,
    'correlation': corr.to_dict(),
    'low_correlation_pairs': low_sorted,
}

with open(OUT_DIR / 'asset_correlation.json', 'w') as f:
    json.dump(out, f, indent=2)

print('\nSaved to', OUT_DIR / 'asset_correlation.json')
