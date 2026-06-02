# TRADING BOT PROJECT MANIFESTO
## A Complete Record of the Journey, Current State, and Future Vision

---

## THE SPEAKER'S WISDOM - The Foundation of Our Approach

Before diving into the technical details, you must understand the philosophical foundation of this project. We are not building a get-rich-quick scheme. We are building a systematic, research-driven trading operation inspired by the principles of professional hedge fund traders and quantitative researchers.

### Core Trading Principles (The "Why")

1. **Explain edge in 5 minutes** - Every strategy must have a clear, logical explanation for why it makes money. Who loses? What do they do wrong? If you can't explain it, you don't have an edge.

2. **3-4 years to know if good** - Reset expectations immediately. Trading is a marathon, not a sprint. You need 3-4 years of data to know if a strategy is genuinely profitable or just lucky.

3. **Comfort vs returns inverse** - Higher returns = less comfort (more volatility). You cannot have both. Accept this reality or quit trading.

4. **Process beats outcome** - Judge yourself on execution, not short-term P&L. Good process + bad outcome = keep going. Bad process + good outcome = dangerous luck.

5. **Portfolio over single strategy** - Need uncorrelated strategies across instruments/timeframes/styles. Single strategies decay. Portfolios survive.

6. **Always improving** - Strategies decay over time as markets adapt. You must keep learning, adapting, and evolving. Static systems die.

7. **Position sizing critical** - Use 1% risk per trade. Aggressive sizing kills accounts. Conservative sizing survives drawdowns.

8. **Drawdowns are cost of game** - Every strategy has drawdowns. Don't panic, don't abandon. Drawdowns are the price you pay for edge.

9. **Overfitting is enemy** - Need out-of-sample testing, parameter sensitivity, walk-forward analysis. If it only works on training data, it's worthless.

10. **Edges decay** - Strategies have half-lives. What works today may not work in 6 months. Adapt or die.

11. **Deployed beats backtest** - Learn more from live deployment than perfect backtests. Real markets have slippage, spreads, and psychology.

12. **Trading is research game** - You are a researcher who occasionally hits buttons, not a discretionary trader. Automate everything.

13. **Automate early** - Consistent execution, emotional detachment, multiple strategies. Humans are the weak link.

---

## WHAT WE WERE DOING - The Journey So Far

### Phase 1: Initial Strategy Development
We started with individual strategies:
- **Smart Money Strategy (SMC)**: Order block-based trading, institutional footprints
- **Mean Reversion**: Z-score based, enters when price deviates from mean
- **Trend Momentum**: EMA slope and crossover based
- **Volatility Breakout**: Volatility expansion trading
- **Breakout Strategy**: Range breakout trading

### Phase 2: Multi-Strategy Portfolio Integration
We realized single strategies are fragile. We built a portfolio engine to:
- Run multiple strategies simultaneously
- Weight strategies by performance
- Manage risk across the portfolio
- Diversify across instruments (EURUSD, GBPUSD, USDJPY, NAS100, XAUUSD, BTCUSD)

### Phase 3: Robustness Testing - The Wake-Up Call
We ran Monte Carlo simulations and discovered:
- Many strategies had no real edge
- Positive returns were often luck
- Parameter sensitivity was high (overfitting)
- Drawdowns were unacceptable

### Phase 4: Strategy Culling and Refinement
We systematically removed underperforming strategies:
- **MACD Strategy**: Created, tested, removed (-4.05% on EURUSD)
- **Bollinger Bands**: Created, tested, removed (0.00% mean return)
- **Volatility Breakout**: Disabled (-4.05% EURUSD, -5.48% XAUUSD)
- **Trend Momentum**: Disabled in favor of RSI/Stochastic

### Phase 5: New Strategy Development
We developed new strategies with different signal generation:
- **RSI Strategy**: RSI oversold/overbought with trend filter
- **Stochastic Strategy**: %K/%D crossovers at extreme levels

### Phase 6: Parameter Optimization
We iterated through parameter versions:
- **v13**: Initial 3-strategy portfolio (mean_reversion, rsi, stochastic)
  - Monte Carlo: 1.30% return, 2.97% drawdown, ratio 0.44
- **v14**: Aggressive tightening (z_threshold 1.8, RSI 20/80, Stochastic 15/85)
  - Monte Carlo: 1.42% return, 1.21% drawdown, ratio 1.17 ✓
  - Problem: Too selective, EURUSD/GBPUSD generated 0 signals
- **v15**: Relaxed parameters (z_threshold 1.5, RSI 25/75, Stochastic 20/80)
  - Monte Carlo: 1.70% return, 2.63% drawdown, ratio 0.65
  - Problem: Stochastic strategy weak (0.04% return, 53.3% positive)
- **v16**: Removed stochastic, 2-strategy portfolio (mean_reversion + rsi)
  - Test in progress

### Phase 7: Advanced Robustness Testing
We added sophisticated testing to prove edge is real, not luck:
- **Walk-Forward Testing**: Out-of-sample validation across time periods
  - Result: EDGE LIKELY REAL (0.80% mean return, 61.7% positive periods)
- **Parameter Sensitivity Analysis**: Test robustness to parameter variations
  - Result: NOT ROBUST (60% positive variations, high sensitivity)
  - Problem: EURUSD/GBPUSD showing 0% returns (parameters too restrictive)

### Phase 8: Workspace Cleanup
We removed all old/unused files to prevent confusion:
- Deleted: macd_strategy.py, bollinger_strategy.py, volatility_strategy.py, etc.
- Deleted: Old test files, old documentation, old data directories
- Kept: Only current strategies, testing infrastructure, and production configs

---

## WHAT WE ARE DOING RIGHT NOW - Current State

### Current Portfolio Configuration (v16)
**Strategies**: 2 strategies (mean_reversion, rsi)
**Instruments**: 6 instruments (EURUSD, GBPUSD, USDJPY, NAS100, XAUUSD, BTCUSD)
**Total Candidates**: 12 (2 strategies × 6 instruments)

### Current Parameters (v16)
**Mean Reversion**:
- window: 14
- z_threshold: 1.5
- stop_atr_mult: 0.7

**RSI**:
- rsi_period: 14
- rsi_oversold: 25.0
- rsi_overbought: 75.0
- stop_atr_mult: 0.7
- tp_atr_mult: 1.8
- max_trend_strength: 0.005

### Current Performance Metrics (v15 - last complete test)
**Monte Carlo Robustness**:
- Mean Return: 1.70%
- Drawdown Mean: 2.63%
- Return/Drawdown Ratio: 0.65 (target > 1.0)
- Positive Return Rate: 60.0%
- Status: GOOD (not EXCELLENT)

**Individual Strategy Performance**:
- mean_reversion: 0.17% mean return, 67.5% positive rate
- rsi: 0.15% mean return, 71.7% positive rate
- stochastic: 0.04% mean return, 53.3% positive rate (REMOVED in v16)

**Walk-Forward Testing**:
- Overall Mean Return: 0.80%
- Mean Drawdown: 1.50%
- Positive Periods: 61.7%
- Conclusion: EDGE LIKELY REAL

**Parameter Sensitivity**:
- Overall Positive Variations: 60.0%
- Conclusion: NOT ROBUST (high parameter sensitivity)

### Current Workspace Structure
**Core Files**:
- main.py: Main trading bot execution (120KB)
- backtest_improved.py: Core backtesting infrastructure (94KB)
- smart_money_strategy.py: Smart money strategy (32KB)
- portfolio_engine.py: Portfolio orchestration (4KB)
- db.py: Database operations (8KB)
- telegram_bot.py: Telegram notifications (3KB)
- requirements.txt: Dependencies

**Current Strategies**:
- mean_reversion.py: Z-score mean reversion (3KB)
- rsi_strategy.py: RSI oversold/overbought (4KB)
- stochastic_strategy.py: Stochastic oscillator (4KB) - currently disabled

**Testing Infrastructure**:
- monte_carlo_robustness.py: Monte Carlo simulations (16KB)
- walk_forward_test.py: Walk-forward testing (9KB)
- parameter_sensitivity.py: Parameter sensitivity analysis (11KB)
- test_signal_generation.py: Signal generation testing (4KB)
- tools/correlation_matrix_from_equities.py: Correlation analysis (4KB)

**Production Configuration**:
- liverun/config/production_strategy_lock.json: Current portfolio config (v16)

---

## WHAT WE ARE PLANNING TO DO - Future Vision

### Immediate Goals (Next 1-2 Weeks)

1. **Complete v16 Testing**
   - Run Monte Carlo on 2-strategy portfolio (mean_reversion + rsi)
   - Target: Return/Drawdown ratio > 1.0
   - If successful: This becomes the production portfolio
   - If unsuccessful: Continue iteration

2. **Improve Return/Drawdown Ratio**
   - Current: 0.65 (1.70% return / 2.63% drawdown)
   - Target: > 1.0 (returns exceed drawdowns)
   - Approaches:
     - Tighten stop losses (reduce drawdowns)
     - Increase take profit targets (increase returns)
     - Add more selective filters (improve win rate)
     - Symbol-specific parameter tuning

3. **Address Parameter Sensitivity**
   - Current: NOT ROBUST (60% positive variations)
   - Target: > 70% positive variations
   - Approaches:
     - Find less sensitive parameters
     - Add robust filters that work across parameter ranges
     - Consider ensemble methods

4. **Fix EURUSD/GBPUSD Signal Generation**
   - Current: 0% returns in walk-forward (no signals)
   - Target: Generate consistent signals
   - Approaches:
     - Symbol-specific parameter tuning
     - Different thresholds for forex vs indices
     - Consider removing forex pairs if they don't work

### Medium-Term Goals (Next 1-3 Months)

1. **Add Correlation Analysis**
   - Measure correlation between strategies
   - Ensure true diversification (low correlation)
   - Remove highly correlated strategies

2. **Market Regime Testing**
   - Test performance in trending vs ranging markets
   - Add regime filters if necessary
   - Ensure strategies work across market conditions

3. **Additional Strategy Development**
   - If current strategies insufficient, develop new ones
   - Focus on uncorrelated signal generation
   - Consider: Volume profile, market microstructure, machine learning

4. **Live Deployment Preparation**
   - Update main.py with final portfolio
   - Update MQL5 EA template for MT5 deployment
   - Prepare for prop firm submission

### Long-Term Vision (3-12 Months)

1. **Continuous Improvement Cycle**
   - Monitor live performance
   - Walk-forward testing on new data
   - Adapt strategies as markets evolve
   - Never stop researching

2. **Multi-Asset Expansion**
   - Add more instruments if edge is robust
   - Consider futures, options, crypto
   - Build truly diversified portfolio

3. **Advanced Risk Management**
   - Dynamic position sizing based on volatility
   - Portfolio-level risk limits
   - Correlation-aware position sizing

4. **Institutional-Grade Infrastructure**
   - Real-time monitoring dashboard
   - Automated alerting
   - Performance attribution
   - Regulatory compliance

---

## THE HARD TRUTHS - What You Must Accept

### This Is Hard
- Trading is one of the most difficult professions
- 90% of traders fail
- Even with automation, success is not guaranteed
- If you're looking for easy money, you're in the wrong place

### This Takes Time
- You need 3-4 years to know if you're good
- You need thousands of trades for statistical significance
- You need to survive drawdowns that last months
- Quick results are usually luck, not skill

### This Requires Discipline
- You must follow your system even when it hurts
- You must accept losses as the cost of doing business
- You must not overtrade when bored
- You must not revenge trade after losses

### This Is Research, Not Gambling
- Every trade is a data point
- Every loss teaches you something
- Every win must be analyzed
- Process > Outcome, always

---

## THE TECHNICAL ARCHITECTURE - How It Works

### Signal Generation Pipeline
1. **Data Fetching**: MT5 API fetches M15 data for each instrument
2. **Resampling**: M15 → H1 (for trend context) and M5 (for signal precision)
3. **Indicator Calculation**: EMA, ATR, ADX, RSI, Stochastic, etc.
4. **Strategy Signal Generation**: Each strategy generates buy/sell signals
5. **Confluence Scoring**: Signals scored by confidence (0-1)
6. **Portfolio Selection**: Best signal selected per instrument
7. **Risk Management**: Position sizing based on 1% risk per trade
8. **Order Execution**: Orders placed via MT5 API

### Strategy Logic

**Mean Reversion**:
- Calculates Z-score of price over 14-period window
- Enters when Z-score > 1.5 (overextended)
- Exits at mean or stop loss
- Edge: Markets revert to mean after extreme moves

**RSI Strategy**:
- Calculates RSI over 14 periods
- Enters when RSI < 25 (oversold) or > 75 (overbought)
- Trend filter: Only trade if trend strength < 0.005
- Edge: RSI extremes predict reversals in ranging markets

**Stochastic Strategy** (currently disabled):
- Calculates %K and %D oscillators
- Enters on %K/%D crossovers at extremes (< 20 or > 80)
- Edge: Momentum changes at overbought/oversold levels

### Risk Management
- **Per-Trade Risk**: 1% of equity
- **Stop Loss**: ATR-based (0.7x ATR for mean_reversion/rsi)
- **Take Profit**: ATR-based (1.8x ATR for rsi)
- **Max Daily Drawdown**: 3% (trading halt)
- **Max Margin Usage**: 20%
- **Consecutive Loss Protection**: Cooldown after 2 consecutive losses

### Testing Infrastructure

**Monte Carlo Robustness**:
- 20 simulations of 30-day periods
- Random start points
- Measures: Mean return, drawdown, positive rate
- Purpose: Test if results are consistent or lucky

**Walk-Forward Testing**:
- 10 sequential time periods
- Out-of-sample validation
- Measures: Consistency across time
- Purpose: Test if edge persists over time

**Parameter Sensitivity**:
- 20 parameter variations (±20%)
- Measures: Robustness to parameter changes
- Purpose: Test if edge is overfitted

---

## THE FILES - What Each File Does

### Core Execution
- **main.py**: Main trading bot. Connects to MT5, runs strategies, manages positions, handles risk. This is what runs 24/7.

### Strategies
- **mean_reversion.py**: Z-score mean reversion strategy. Enters when price deviates significantly from mean.
- **rsi_strategy.py**: RSI-based strategy. Enters at RSI extremes with trend filter.
- **stochastic_strategy.py**: Stochastic oscillator strategy. Enters on %K/%D crossovers at extremes.
- **smart_money_strategy.py**: Order block and institutional footprint strategy. More complex, SMC-based.

### Infrastructure
- **backtest_improved.py**: Core backtesting engine. Fetches data, calculates indicators, simulates trades.
- **portfolio_engine.py**: Portfolio orchestration. Manages multiple strategies, risk, and position sizing.
- **db.py**: SQLite database operations. Stores trades, logs, order blocks.
- **telegram_bot.py**: Telegram notifications. Sends alerts for trades, errors, status updates.

### Testing
- **monte_carlo_robustness.py**: Monte Carlo simulations. Tests consistency across random periods.
- **walk_forward_test.py**: Walk-forward testing. Tests out-of-sample performance across time.
- **parameter_sensitivity.py**: Parameter sensitivity analysis. Tests robustness to parameter changes.
- **test_signal_generation.py**: Signal generation testing. Diagnoses why strategies aren't generating signals.

### Configuration
- **liverun/config/production_strategy_lock.json**: Production portfolio configuration. Defines which strategies run on which instruments with which parameters.

### Tools
- **tools/correlation_matrix_from_equities.py**: Correlation analysis. Measures correlation between strategy equity curves.

---

## THE DEPLOYMENT PATH - From Development to Production

### Development Phase
- Strategy development in Python
- Backtesting on historical data
- Monte Carlo robustness testing
- Walk-forward validation
- Parameter sensitivity analysis

### Paper Trading Phase
- Run strategies in simulation mode
- Monitor real-time performance
- Validate execution logic
- Test risk management
- No real money at risk

### Live Trading Phase
- Deploy to MT5 with small position sizes
- Monitor closely for 1-2 months
- Validate slippage, spreads, execution
- Scale up if performance matches backtest

### Prop Firm Phase
- Submit to prop firm evaluation
- Pass evaluation (8% profit, 5% max drawdown)
- Trade funded account
- Scale to full position sizes

### MQL5 Deployment
- Port Python strategies to MQL5
- Compile to .ex5
- Deploy to MT5 VPS
- Run 24/7 without Python

---

## THE LESSONS LEARNED - What We Got Wrong

### Mistake 1: Too Many Strategies Initially
- We started with 6+ strategies
- Most had no real edge
- Wasted time optimizing losing strategies
- **Lesson**: Start simple, add only what proves profitable

### Mistake 2: Overfitting Parameters
- We tuned parameters to maximize backtest returns
- Strategies failed on new data
- High parameter sensitivity
- **Lesson**: Focus on robustness, not optimization

### Mistake 3: Ignoring Drawdowns
- We focused too much on returns
- Accepted high drawdowns for higher returns
- Real trading would have blown accounts
- **Lesson**: Drawdowns matter more than returns

### Mistake 4: Not Testing Enough
- We relied on single backtests
- Didn't do Monte Carlo or walk-forward
- Results were not statistically significant
- **Lesson**: Multiple testing methods required

### Mistake 5: One-Size-Fits-All Parameters
- We used same parameters for all instruments
- EURUSD and NAS100 have different characteristics
- Some instruments generated 0 signals
- **Lesson**: Symbol-specific tuning required

---

## THE FINAL WORD - What You Must Remember

### This Is a Research Project
We are not traders. We are quantitative researchers building an automated trading system. The difference is crucial.

### Process Over Outcome
If you follow the process correctly, good outcomes will eventually follow. If you chase outcomes, you will fail.

### Edge Is Fragile
Markets adapt. Strategies decay. What works today may not work tomorrow. You must adapt faster than the market.

### Risk Is Everything
Position sizing, risk management, drawdown control - these are more important than entry signals. You can survive with mediocre entries. You cannot survive with poor risk management.

### Never Stop Learning
The moment you think you've figured it out, you're wrong. Stay humble, stay curious, keep researching.

### The Goal Is Not Quick Riches
The goal is to build a sustainable, systematic trading operation that generates consistent returns over years. If you're looking for quick money, go to a casino.

### This Is Hard, But Worth It
If it were easy, everyone would do it. The difficulty is the barrier to entry. If you can solve this puzzle, you've achieved something few ever will.

---

## THE CONTACT - If You Need Help

If you're reading this and need to continue this work, here's what you need to know:

### Prerequisites
- Python 3.11+
- MetaTrader 5 installed and connected
- MT5 account with data access
- Understanding of trading basics
- Patience and discipline

### First Steps
1. Read all the strategy files to understand logic
2. Run monte_carlo_robustness.py to test current portfolio
3. Analyze results in liverun/config/production_strategy_lock.json
4. Iterate parameters based on results
5. Never skip robustness testing

### Key Commands
```bash
# Monte Carlo testing
python monte_carlo_robustness.py --bars 50000 --limit 12 --simulations 20 --period-days 30

# Walk-forward testing
python walk_forward_test.py

# Parameter sensitivity
python parameter_sensitivity.py

# Signal generation test
python test_signal_generation.py

# Run main bot
python main.py
```

### Important Numbers to Remember
- Risk per trade: 1%
- Max daily drawdown: 3%
- Target return/drawdown ratio: > 1.0
- Target positive rate: > 65%
- Target parameter sensitivity: > 70%

### The Golden Rule
If you can't explain why a strategy makes money in 5 minutes, don't trade it.

---

## THE END - Final Thoughts

This project is not about building a trading bot. It's about building a systematic, research-driven approach to the markets that can survive the test of time.

The journey is long. The work is hard. The failures are frequent. But if you stick to the process, respect the risk, and never stop learning, you might just build something that lasts.

Good luck. You'll need it.

---

*Document written on June 1, 2026*
*Project: Multi-Strategy Trading Bot*
*Status: In Development - v16 Testing Phase*
*Next Milestone: Achieve Return/Drawdown Ratio > 1.0*
