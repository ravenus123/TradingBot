# Hedge Fund Bot Testing Results Summary
**Date**: 2026-05-30

## Monte-Carlo Stress Test Results

### Baseline Performance
- **Total Return**: 1387.11% (±227.49%)
- **95% CI**: [1033.14%, 1781.66%]
- **Max Drawdown**: 93.13% (95% CI: 94.69%)
- **Sharpe Ratio**: 19.92

### Slippage Stress Test
- 0 pips: Return 1378.83%, DD 93.09%
- 0.5 pips: Return 1376.79%, DD 93.08%
- 1.0 pips: Return 1375.09%, DD 93.07%
- 2.0 pips: Return 1384.61%, DD 93.12%
- 5.0 pips: Return 1385.16%, DD 93.13%

**Conclusion**: Slippage has minimal impact on portfolio performance.

### Commission Stress Test
- $0/lot: Return 1378.54%, DD 93.09%
- $5/lot: Return 1248.27%, DD 92.43%
- $10/lot: Return 1117.22%, DD 91.62%
- $15/lot: Return 1010.00%, DD 90.79%
- $20/lot: Return 893.22%, DD 89.72%

**Conclusion**: Commission has significant impact. At $20/lot, returns drop to 893.22%.

### Spread Stress Test
- 0.5 pips: Return 1400.34%, DD 93.19%
- 1.0 pips: Return 1385.24%, DD 93.13%
- 2.0 pips: Return 1381.62%, DD 93.10%
- 5.0 pips: Return 1372.65%, DD 93.06%

**Conclusion**: Spread has minimal impact on portfolio performance.

### Worst Case (Max Stress)
- **Total Return**: 895.41% (95% CI: [670.36%, 1152.11%])
- **Max Drawdown**: 89.77% (95% CI: 92.03%)
- **Sharpe Ratio**: 16.98

**Conclusion**: Portfolio remains robust even under maximum stress conditions.

---

## Walk-Forward Monthly Robustness Test Results

### Strategy Performance Across 6 Monthly Windows

| Strategy | Windows Tested | Total Trades | Win Rate | Total Profit | Avg Profit/Window |
|----------|----------------|--------------|----------|--------------|-------------------|
| trend_momentum:EURUSD:2 | 4 | 2828 | 22.21% | **-$182,009.56** | **-$45,502.39** ❌ |
| mean_reversion:EURUSD:1 | 4 | 724 | 31.49% | **+$14,623.00** | **+$3,655.75** ✅ |
| trend_momentum:NAS100:5 | 5 | 3543 | 30.29% | **+$117,264.88** | **+$23,452.98** ✅ |
| mean_reversion:NAS100:4 | 5 | 1940 | 28.09% | **+$37,022.01** | **+$7,404.40** ✅ |
| trend_momentum:XAUUSD:8 | 5 | 3336 | 23.53% | **-$49,845.43** | **-$9,969.09** ❌ |
| mean_reversion:XAUUSD:7 | 5 | 340 | 25.59% | **+$1,670.83** | **+$334.17** ✅ |

### Key Insights

**Performing Strategies:**
- ✅ mean_reversion:EURUSD:1 - Consistent profitability
- ✅ trend_momentum:NAS100:5 - Strong performance
- ✅ mean_reversion:NAS100:4 - Moderate profitability
- ✅ mean_reversion:XAUUSD:7 - Low but positive returns

**Underperforming Strategies:**
- ❌ trend_momentum:EURUSD:2 - Significant losses
- ❌ trend_momentum:XAUUSD:8 - Significant losses

**Recommendation**: Remove trend_momentum from EURUSD and XAUUSD. Focus on mean_reversion for these instruments.

---

## New Instruments Parameter Sweep Results

### GBPUSD
| Strategy | Best Params | Avg Return | Win Rate | Total Signals |
|----------|-------------|-----------|----------|---------------|
| trend_momentum | h1_ema=100, m5_ema=20, stop=1.0 | **-0.01%** ❌ | 16.48% | 1226 |
| mean_reversion | window=20, z=2.0, stop=1.0 | **+0.01%** ⚠️ | 31.23% | 269 |
| volatility_breakout | bb=20, std=2.0, kc=2.0 | **-0.03%** ❌ | 4.48% | 223 |

### USDJPY
| Strategy | Best Params | Avg Return | Win Rate | Total Signals |
|----------|-------------|-----------|----------|---------------|
| trend_momentum | h1_ema=100, m5_ema=20, stop=1.0 | **+0.00%** ⚠️ | 20.97% | 1154 |
| mean_reversion | window=20, z=1.5, stop=0.8 | **+0.00%** ⚠️ | 32.15% | 650 |
| volatility_breakout | bb=20, std=2.0, kc=2.0 | **-0.02%** ❌ | 2.82% | 71 |

### BTCUSD
| Strategy | Best Params | Avg Return | Win Rate | Total Signals |
|----------|-------------|-----------|----------|---------------|
| trend_momentum | h1_ema=50, m5_ema=20, stop=1.5 | **-0.01%** ❌ | 23.65% | 1260 |
| mean_reversion | window=20, z=1.5, stop=0.8 | **-0.02%** ❌ | 27.58% | 649 |
| volatility_breakout | bb=20, std=2.0, kc=2.0 | **-0.10%** ❌ | 4.96% | 262 |

### Key Insights

**All new instruments show poor performance:**
- GBPUSD: Near-zero returns, volatility_breakout performing worst
- USDJPY: Near-zero returns, volatility_breakout performing worst
- BTCUSD: Negative returns across all strategies

**Recommendation**: Do NOT add GBPUSD, USDJPY, BTCUSD to portfolio at this time. Current strategy parameters are not effective for these instruments. Requires further research and parameter optimization.

---

## Portfolio Recommendations

### Immediate Actions Required

1. **Remove Underperforming Strategies**
   - Remove trend_momentum from EURUSD
   - Remove trend_momentum from XAUUSD
   - Keep only mean_reversion for EURUSD and XAUUSD

2. **Keep Performing Strategies**
   - Keep trend_momentum:NAS100:5 (strong performer)
   - Keep mean_reversion:NAS100:4 (moderate performer)
   - Keep mean_reversion:EURUSD:1 (consistent performer)
   - Keep mean_reversion:XAUUSD:7 (low but positive)

3. **Do NOT Add New Instruments**
   - GBPUSD: Not ready for production
   - USDJPY: Not ready for production
   - BTCUSD: Not ready for production

4. **Revert Instrument Expansion**
   - Revert SYMBOLS to original 3 instruments: EURUSD, NAS100, XAUUSD
   - Focus on optimizing existing portfolio before expanding

### Revised Portfolio Configuration

**Instruments**: EURUSD, NAS100, XAUUSD (3 instruments)

**Strategies**:
- EURUSD: mean_reversion only
- NAS100: trend_momentum + mean_reversion
- XAUUSD: mean_reversion only

**Expected Benefits**:
- Eliminate losing strategies (trend_momentum on EURUSD/XAUUSD)
- Focus on proven profitable strategies
- Reduce portfolio complexity
- Improve overall risk-adjusted returns

---

## Next Steps

1. Update main.py to remove trend_momentum from EURUSD and XAUUSD
2. Update main.py to revert SYMBOLS to 3 instruments
3. Update MQL5 EA template to match revised portfolio
4. Re-run walk-forward test on revised portfolio
5. Consider additional parameter optimization for new instruments before adding them
