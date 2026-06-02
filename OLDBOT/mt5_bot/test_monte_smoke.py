import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from monte_carlo_robustness import build_stepwise_equity

# Build synthetic M15 bars
n = 600
end = datetime.utcnow()
idx = [end - timedelta(minutes=15 * (n - i)) for i in range(n)]
close = np.cumsum(np.random.randn(n) * 0.0005) + 1.1000
openp = close + np.random.randn(n) * 0.0001
high = np.maximum(openp, close) + np.abs(np.random.randn(n) * 0.0002)
low = np.minimum(openp, close) - np.abs(np.random.randn(n) * 0.0002)
vol = np.random.randint(10, 100, size=n)

df = pd.DataFrame({'Open': openp, 'High': high, 'Low': low, 'Close': close, 'Volume': vol}, index=pd.DatetimeIndex(idx))

def make_candidate():
    return {
        'symbol': 'EURUSD',
        'strategy': 'mean_reversion',
        'params': {
            'z_threshold': 1.5,
            'window': 14,
            'stop_atr_mult': 0.8,
        }
    }

cand = make_candidate()
equity, trades = build_stepwise_equity(cand, df, risk_pct=1.0)
print('Equity length:', len(equity))
print('Final equity:', equity.iloc[-1])
print('Trades count:', len(trades))
print('Trades sample:', trades[:10])
