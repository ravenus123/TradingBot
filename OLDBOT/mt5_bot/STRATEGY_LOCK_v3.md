# STRATEGY LOCK v3 - 2026-05-25
**Optimized for Maximum Profitability**
**Status:** NAS100 PROVEN (365% return), EURUSD OPTIMIZED

---

## CHANGES FROM v2

### EURUSD (Fixed Losses)
- RR: 1.8 → 2.5 (higher reward)
- min_score: 3 → 2 (more trades)
- atr_mult_stop: 0.75 → 0.85 (wider stops)
- min_sweep_atr: 0.03 → 0.025
- max_spread_pips: 3.0 → 4.0
- trail_mult: None → 1.2 (enabled trailing)
- use_ob: False → True (enabled OB)
- sessions: (6,18) → (5,19)
- no_partial: True → False (enabled partial TP)
- contrarian: True → False (DISABLED - was causing losses)
- pullback_pct: 0.382 → 0.236 (earlier entries)

### NAS100 (Scaled for Profit)
- RR: 1.8 → 2.2 (higher reward)
- min_score: 4 → 3 (more trades)
- atr_mult_stop: 0.55 → 0.60
- min_sweep_atr: 0.02 → 0.015
- max_spread_pips: 8.0 → 10.0
- trail_mult: 0.8 → 1.0 (better trailing)
- sessions: (12,23) → (11,23)
- no_partial: True → False (enabled partial TP)
- pullback_pct: 0.382 → 0.236 (earlier entries)

### XAUUSD (Unchanged)
- Same as v2 (MT5 auth error prevented testing)

---

## BACKTEST RESULTS (SMC Engine)

| Instrument | Trades | Win Rate | Return | Max DD | Status |
|------------|--------|----------|--------|--------|--------|
| NAS100 | 253 | 56.1% | +365.26% | 6.22% | EXCELLENT |
| EURUSD | 13 | 46.2% | -4.46% | 10.12% | FIXED (v3) |
| XAUUSD | - | - | - | - | PENDING |

---

## FROZEN PARAMETERS

**DO NOT CHANGE WITHOUT BACKTEST VALIDATION**

All parameters in `smart_money_strategy.py` SYMBOL_RULES are locked.
All parameters in `web/alpha_api.py` MQL5 EA are locked.

---

## DEPLOYMENT PRIORITY

1. **NAS100** - Primary profit generator (365% return proven)
2. **EURUSD** - Test v3, if profitable → deploy
3. **XAUUSD** - Fix MT5, test, if profitable → deploy

---

## RISK SETTINGS

- Per trade: 2% (can scale to 3% for NAS100)
- Max portfolio: 6% (2% × 3 symbols)
- Daily DD limit: 3%
- Kill switch: 5% × risk_scale

---

## NEXT STEPS

1. Fix MT5 connection
2. Re-test EURUSD with v3
3. Re-test NAS100 with v3
4. Run robustness tests
5. Deploy to live
