# Multi-Strategy Portfolio Architecture

## What is now in place

- Modular strategy registry and orchestrator in `portfolio_engine.py`
  - `StrategyRegistry` for strategy modules.
  - `PortfolioOrchestrator` for selecting candidate signals.
  - `PortfolioRiskManager` to cap risk-per-trade at `1.0%`.
- Live bot integration in `main.py`
  - Signals are now generated through the orchestrator.
  - Strategy metadata (`strategy_name`, style, weight) is attached to each signal.
  - Portfolio config includes placeholders for:
    - `smart_money_v1` (enabled)
    - `mean_reversion_v1` (disabled placeholder)
    - `trend_momentum_v1` (disabled placeholder)
    - `breakout_v1` (disabled placeholder)
- Strategy-aware trade persistence in `db.py`
  - Trades table now has `strategy_name`.
  - Existing databases auto-migrate (`ALTER TABLE ... ADD COLUMN strategy_name`).
  - Added strategy/time index for research queries.
- Correlation research utility in `portfolio_research.py`
  - Builds daily-PnL strategy correlation matrix from closed trades.
  - Flags highly correlated pairs (`|corr| >= 0.8`) as cull candidates.

## Research lifecycle

1. Implement a new strategy module and register it.
2. Paper/deploy at small size (<= 1% risk per trade).
3. Collect trade logs with strategy attribution.
4. Run correlation matrix and cull highly correlated variants.
5. Iterate or retire decayed strategies.

## Usage

```powershell
# Correlation analysis from live closed trades
python OLDBOT/mt5_bot/portfolio_research.py --limit 5000

# Save matrix JSON to file
python OLDBOT/mt5_bot/portfolio_research.py --limit 5000 --out OLDBOT/mt5_bot/liverun/strategy_corr.json
```
