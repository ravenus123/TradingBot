Finaler Action Plan — Translating the 13 Lessons into the repo
===============================================================

This file maps the 13 lessons from "Finaler" (YouTube transcript) into concrete, prioritized engineering and process tasks for this repo.

Priority 1 — Safety, selection, sizing
- Enforce a production gate: a strategy only becomes `production` when it passes Monte Carlo + walk‑forward & sensitivity tests.
- Default position sizing: enforce 1% risk per trade (configurable) across equity generators and live sizing code.
- Add conservative caps (max position size, max leverage) and an emergency kill switch file `production_strategy_lock.json`.

Priority 2 — Robustness & validation
- Run large Monte Carlo ensembles (500–2000) with trade bootstrap, shuffled returns, slippage/noise sweeps.
- Walk‑forward with rolling train/test and step sizes; compute stability-adjusted scoring (mean train performance / train volatility).
- Parameter sensitivity sweeps and remove overfit candidates (high parameter sensitivity, low out‑of‑sample performance).

Priority 3 — Portfolio construction
- Treat a portfolio as the business: diversify across instruments, styles, timeframes.
- Compute correlations between strategy return series and allocate capital by inverse volatility and correlation considerations.

Priority 4 — Operate like a researcher
- Automate experiment logging (seed, window, shock params) to `liverun/` with reproducible metadata.
- Continuous monitoring: daily job generates correlation matrices, drawdown percentiles, and top‑k worst Monte Carlo runs.

Priority 5 — Ship & Iterate
- Deploy small: ship with tiny-sized live allocations to learn real slippage and API quirks.
- Automate CI for backtest-to-live pipeline: build → smoke Monte Carlo → gated deploy.

Immediate engineering tasks (next actions I'll implement)
1. Add a lightweight tool that computes a correlation matrix from recent `equity_*.csv` runs and saves CSV + PNG (helps verify diversification).
2. Make `equity_curve_stepwise.py` and `robustness_runner.py` default to a conservative `risk_pct=1.0` (already present) and add clear README run commands.
3. Add a checklist for production gating in `liverun/README_PRODUCTION.md` describing pass criteria (e.g., gate: dd<=25% and final_balance>=75% across >=X% runs).

Run instructions (examples)
```
python OLDBOT/mt5_bot/equity_curve_stepwise.py --bars 8000 --period-bars 0 --limit 3 --seed 13
python OLDBOT/mt5_bot/robustness_runner.py --runs 500 --bars 8000 --seed 13 --workers 8
python OLDBOT/mt5_bot/tools/correlation_matrix_from_equities.py --dir OLDBOT/mt5_bot/equity_curves
```

If you'd like, I can now:
- Run a quick correlation scan on the latest equity CSVs (if you permit running commands).
- Start a Monte Carlo job with the conservative gate and save the run artifacts.

— I'll await your confirmation to run heavy Monte Carlo jobs (they can be CPU‑intensive).
