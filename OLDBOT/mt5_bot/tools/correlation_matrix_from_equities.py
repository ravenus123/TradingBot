"""Compute correlation matrix and heatmap from saved equity CSVs.

Usage:
    python OLDBOT/mt5_bot/tools/correlation_matrix_from_equities.py --dir OLDBOT/mt5_bot/equity_curves

The script loads CSVs with columns `time` and `equity`, aligns them on a common datetime index,
computes percentage returns, then saves `correlation_matrix.csv` and `correlation_heatmap.png` in the same folder.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=str, default="OLDBOT/mt5_bot/equity_curves")
    parser.add_argument("--pattern", type=str, default="equity_*_*.csv")
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args()

    folder = Path(args.dir)
    if not folder.exists():
        print("Folder not found:", folder)
        return

    files = sorted(folder.glob(args.pattern))
    if not files:
        # fallback: load equity_*.csv without run tag
        files = sorted(folder.glob("equity_*.csv"))
    if not files:
        print("No equity CSV files found in", folder)
        return

    series_map = {}
    for f in files:
        try:
            df = pd.read_csv(f)
            if "time" not in df.columns or "equity" not in df.columns:
                continue
            df["time"] = pd.to_datetime(df["time"], errors="coerce")
            df = df.set_index("time").sort_index()
            # compute daily returns for correlation stability (resample to daily)
            df_daily = df["equity"].resample("D").last().ffill()
            name = f.stem
            series_map[name] = df_daily
        except Exception as e:
            print("Skipping", f, "->", e)

    if not series_map:
        print("No usable series found")
        return

    # align on common index
    idx = pd.Index([])
    for s in series_map.values():
        idx = idx.union(s.index)
    idx = idx.sort_values()

    df_all = pd.DataFrame(index=idx)
    for name, s in series_map.items():
        df_all[name] = s.reindex(idx).ffill()

    # compute percent returns
    returns = df_all.pct_change().fillna(0.0)

    corr = returns.corr()

    out_folder = Path(args.out) if args.out else folder
    out_folder.mkdir(parents=True, exist_ok=True)

    corr_csv = out_folder / "correlation_matrix.csv"
    corr_png = out_folder / "correlation_heatmap.png"

    corr.to_csv(corr_csv)

    # plot heatmap
    try:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(8, 6))
        im = plt.imshow(corr.values, cmap="RdYlGn", vmin=-1, vmax=1)
        plt.colorbar(im, fraction=0.046, pad=0.04)
        ticks = np.arange(len(corr.columns))
        plt.xticks(ticks, corr.columns, rotation=90, fontsize=8)
        plt.yticks(ticks, corr.columns, fontsize=8)
        for i in range(len(corr.columns)):
            for j in range(len(corr.columns)):
                txt = f"{corr.values[i, j]:.2f}"
                plt.text(j, i, txt, ha='center', va='center', color='black', fontsize=7)
        plt.title("Correlation Matrix (daily returns)")
        plt.tight_layout()
        plt.savefig(corr_png, dpi=150)
        plt.close()
        print("Saved:", corr_csv, corr_png)
    except Exception as e:
        print("Saved CSV but failed to render PNG:", e)


if __name__ == '__main__':
    main()
