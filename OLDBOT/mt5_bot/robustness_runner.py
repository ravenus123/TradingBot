"""Robustness runner: Monte Carlo random-period sampling and basic walk-forward stub.

Produces per-run equity CSVs, a portfolio summary JSON, and distribution plots.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import time
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import concurrent.futures

ROOT = Path(__file__).resolve().parents[2]
BOT_DIR = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BOT_DIR))

from equity_curve_stepwise import load_candidates, build_stepwise_equity


def max_drawdown(equity: List[float]) -> float:
    peak = -math.inf
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return (max_dd / peak * 100.0) if peak and peak > 0 else 0.0


def _bootstrap_trade_series(
    orig_series: pd.Series,
    trade_returns: List[float],
    rng: random.Random,
    slippage_bps: float = 0.0,
    commission_bps: float = 0.0,
    noise_bps: float = 0.0,
    shuffle_returns: bool = False,
) -> pd.Series:
    """Given an original series and its trade returns list, resample returns and rebuild series."""
    if len(trade_returns) == 0:
        return orig_series

    vals = orig_series.values.copy()
    # find step positions where value changes
    diffs = np.diff(vals)
    step_positions = np.where(diffs != 0)[0] + 1
    if len(step_positions) == 0:
        return orig_series

    sampled = [rng.choice(trade_returns) for _ in range(len(step_positions))]
    if shuffle_returns:
        rng.shuffle(sampled)
    new_vals = vals.copy()
    prev = float(vals[0])
    for i, pos in enumerate(step_positions):
        r = sampled[i]
        if noise_bps > 0:
            r += rng.gauss(0.0, noise_bps / 100.0)
        if slippage_bps > 0:
            r -= slippage_bps / 100.0
        if commission_bps > 0:
            r -= commission_bps / 100.0
        next_balance = prev * (1.0 + (r / 100.0))
        new_vals[pos] = next_balance
        prev = next_balance
    # forward fill
    for i in range(1, len(new_vals)):
        if math.isnan(new_vals[i]):
            new_vals[i] = new_vals[i-1]

    return pd.Series(new_vals, index=orig_series.index)


def _run_single(
    run: int,
    candidates: List[Dict],
    outdir: Path,
    bars: int,
    min_days: int,
    max_days: int,
    risk_pct: float,
    seed: int | None,
    trade_bootstrap: bool,
    slippage_bps: float,
    commission_bps: float,
    noise_bps: float,
    shuffle_returns: bool,
):
    rng = random.Random(seed + run if seed is not None else None)
    run_tag = f"run_{run:03d}"
    run_dir = outdir / run_tag
    run_dir.mkdir(parents=True, exist_ok=True)

    per_inst_series = {}
    per_inst_trades = {}

    for c in candidates:
        try:
            # Use completely random period across entire available history
            # min_days and max_days control the period length, but start position is random
            s, trades = build_stepwise_equity(c, period_bars=None, bars=bars, risk_pct=risk_pct, rng=rng, min_days=min_days, max_days=max_days)
        except Exception as e:
            print(f"Run {run} {c['symbol']} skipped: {e}")
            s = pd.Series(dtype=float)
            trades = []

        per_inst_series[c['symbol']] = s
        per_inst_trades[c['symbol']] = trades
        csvp = run_dir / f"equity_{c['symbol']}.csv"
        pd.DataFrame({"time": s.index, "equity": s.values}).to_csv(csvp, index=False)

    # align
    common_index = pd.Index([])
    for s in per_inst_series.values():
        common_index = common_index.union(s.index)
    common_index = common_index.sort_values()
    if len(common_index) == 0:
        return None

    equity_df = pd.DataFrame(index=common_index)
    start_cap = 100000.0
    n_sym = len(per_inst_series)
    cap_each = start_cap / max(1, n_sym)

    for sym, s in per_inst_series.items():
        s2 = s.reindex(common_index).ffill().fillna(10000.0)
        if trade_bootstrap and len(per_inst_trades.get(sym, [])) > 0:
            s2 = _bootstrap_trade_series(
                s2,
                per_inst_trades[sym],
                rng,
                slippage_bps=slippage_bps,
                commission_bps=commission_bps,
                noise_bps=noise_bps,
                shuffle_returns=shuffle_returns,
            )
        base = float(s2.iloc[0]) if len(s2) else 10000.0
        norm = (s2 / base) if base != 0 else s2 * 0 + 1.0
        equity_df[sym] = norm * cap_each

    portfolio = equity_df.sum(axis=1)
    final_balance = float(portfolio.iloc[-1])
    dd = max_drawdown(list(portfolio.values))
    trades_total = sum(len(v) for v in per_inst_trades.values())

    summary = {
        "run": run,
        "final_balance": final_balance,
        "max_drawdown_pct": dd,
        "trades_total": trades_total,
        "per_instrument_trades": {k: len(v) for k, v in per_inst_trades.items()},
    }

    pd.DataFrame({"time": portfolio.index, "portfolio_equity": portfolio.values}).to_csv(run_dir / "portfolio.csv", index=False)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def run_monte_carlo(
    runs: int,
    candidates: List[Dict],
    outdir: Path,
    bars: int,
    min_days: int,
    max_days: int,
    seed: int | None,
    risk_pct: float,
    workers: int = 1,
    trade_bootstrap: bool = False,
    slippage_bps: float = 0.0,
    commission_bps: float = 0.0,
    noise_bps: float = 0.0,
    shuffle_returns: bool = False,
):
    outdir.mkdir(parents=True, exist_ok=True)
    summaries = []
    # prepare args for workers
    args = [
        (
            i,
            candidates,
            outdir,
            bars,
            min_days,
            max_days,
            risk_pct,
            seed,
            trade_bootstrap,
            slippage_bps,
            commission_bps,
            noise_bps,
            shuffle_returns,
        )
        for i in range(1, runs + 1)
    ]

    if workers <= 1:
        for a in args:
            res = _run_single(*a)
            if res:
                summaries.append(res)
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_run_single, *a) for a in args]
            for f in concurrent.futures.as_completed(futures):
                try:
                    res = f.result()
                    if res:
                        summaries.append(res)
                except Exception as e:
                    print("Worker error:", e)

    df_res = pd.DataFrame(summaries)
    if df_res.empty:
        print("No Monte Carlo results")
        return

    # simple decision rules for robustness gating
    dd_limit = 25.0
    floor_limit = 75000.0
    df_res["pass_gate"] = (df_res["max_drawdown_pct"] <= dd_limit) & (df_res["final_balance"] >= floor_limit)

    df_res.to_csv(outdir / "monte_carlo_summary.csv", index=False)
    plt.figure(figsize=(8, 4))
    plt.hist(df_res["final_balance"].values, bins=30)
    plt.title("Monte Carlo Final Balance Distribution")
    plt.xlabel("Final Balance ($)")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(outdir / "monte_carlo_final_balance_hist.png", dpi=150)

    percentiles = np.percentile(df_res["final_balance"].values, [1, 5, 10, 25, 50, 75, 90, 95, 99]).tolist()
    stats = {
        "runs": runs,
        "final_balance_mean": float(df_res["final_balance"].mean()),
        "final_balance_median": float(df_res["final_balance"].median()),
        "final_balance_percentiles": percentiles,
        "dd_limit": dd_limit,
        "floor_limit": floor_limit,
        "pass_rate": float(df_res["pass_gate"].mean()),
        "slippage_bps": slippage_bps,
        "commission_bps": commission_bps,
        "noise_bps": noise_bps,
        "shuffle_returns": shuffle_returns,
    }
    (outdir / "monte_carlo_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print("Monte Carlo complete. Summary saved to", outdir)
    print(f"Gate pass rate: {stats['pass_rate']:.1%} (dd<={dd_limit}% and final_balance>={floor_limit:,.0f})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--bars", type=int, default=50000, help="Total bars to fetch (50000 = ~1 year of M15 data)")
    parser.add_argument("--limit", type=int, default=0, help="0=all candidates")
    parser.add_argument("--out", type=str, default=str(BOT_DIR / "liverun" / "robustness"))
    parser.add_argument("--min-days", type=int, default=30, help="Minimum random period in days")
    parser.add_argument("--max-days", type=int, default=365, help="Maximum random period in days")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--trade-bootstrap", action="store_true")
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--commission-bps", type=float, default=2.0)
    parser.add_argument("--noise-bps", type=float, default=3.0)
    parser.add_argument("--shuffle-returns", action="store_true")
    args = parser.parse_args()

    candidates = load_candidates(limit=args.limit if args.limit > 0 else None)
    if not candidates:
        print("No candidates found; aborting")
        return

    outdir = Path(args.out) / f"run_{int(time.time())}"
    run_monte_carlo(
        args.runs,
        candidates,
        outdir,
        args.bars,
        args.min_days,
        args.max_days,
        args.seed,
        args.risk_pct,
        workers=args.workers,
        trade_bootstrap=args.trade_bootstrap,
        slippage_bps=args.slippage_bps,
        commission_bps=args.commission_bps,
        noise_bps=args.noise_bps,
        shuffle_returns=args.shuffle_returns,
    )


if __name__ == '__main__':
    main()
