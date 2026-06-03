"""
Momentum Premium Strategy

EDGE EXPLANATION (5 minutes):
==============================
WHY IT MAKES MONEY:
- Markets have persistence - trends tend to continue (momentum effect)
- Risk premium: You get paid for holding positions through volatility and drawdowns
- Institutional algo flows create sustained momentum as they execute large orders
- Behavioral bias: Traders underreact to new information, creating sustained trends

WHO'S ON THE LOSING SIDE:
- Traders who fade momentum (counter-trend traders) get crushed
- Traders with tight stops in trending markets get shaken out
- Retail traders trying to pick tops and bottoms without understanding trend structure
- Mean reversion traders in strongly trending markets

WHAT THEY'RE DOING WRONG:
- Fighting the trend instead of following it
- Taking profits too early in strong momentum moves
- Not understanding that trends can go much further than expected
- Over-trading counter-trend setups in trending markets

STRATEGY LOGIC:
===============
1. Identify trend strength using multiple timeframes
2. Enter on momentum continuation patterns
3. Use wider stops to survive volatility (risk premium)
4. Target larger moves as trends can extend significantly
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_momentum_premium_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    Momentum Premium Strategy: Captures sustained trends for risk premium.
    
    Edge: Markets have persistence - trends tend to continue (risk premium).
    Who loses: Traders who fade momentum and pick tops/bottoms.
    """
    try:
        p = params or {}
        
        # Use both timeframes for trend confirmation
        h1 = add_indicators(df_1h, ema_period=20).fillna(0)
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        
        if len(h1) < 100 or len(m5) < 50:
            return None
        
        # H1 trend analysis (primary trend)
        h1_close = h1['Close'].astype(float)
        h1_ema_fast = h1_close.ewm(span=int(p.get('ema_fast', 50)), adjust=False).mean()
        h1_ema_slow = h1_close.ewm(span=int(p.get('ema_slow', 200)), adjust=False).mean()
        h1_trend = 1 if h1_ema_fast.iloc[-1] > h1_ema_slow.iloc[-1] else -1
        
        # M5 momentum analysis (entry timing)
        m5_close = m5['Close'].astype(float)
        m5_high = m5['High'].astype(float)
        m5_low = m5['Low'].astype(float)
        
        # Calculate momentum strength
        m5_momentum = m5_close.pct_change(int(p.get('momentum_period', 10)))
        m5_atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(m5_close.iloc[-1]) * 0.001)
        
        # Recent price action
        recent_high = m5_high.rolling(window=int(p.get('lookback', 24))).max().iloc[-2]
        recent_low = m5_low.rolling(window=int(p.get('lookback', 24))).min().iloc[-2]
        current_close = float(m5_close.iloc[-1])
        
        # Momentum continuation logic
        momentum_threshold = float(p.get('momentum_threshold', 0.001))
        current_momentum = float(m5_momentum.iloc[-1])
        
        direction = None
        if h1_trend == 1 and current_momentum > momentum_threshold and current_close > recent_high:
            direction = 'BUY'
        elif h1_trend == -1 and current_momentum < -momentum_threshold and current_close < recent_low:
            direction = 'SELL'
        else:
            return None
        
        # Risk management with wider stops (risk premium)
        stop_mult = float(p.get('stop_atr_mult', 2.5))  # Wider stops for trend following
        tp_mult = float(p.get('tp_atr_mult', 6.0))  # Larger targets (trends extend)
        
        entry = current_close
        stop = entry - m5_atr * stop_mult if direction == 'BUY' else entry + m5_atr * stop_mult
        tp = entry + m5_atr * tp_mult if direction == 'BUY' else entry - m5_atr * tp_mult
        
        # Confluence based on trend strength and momentum
        trend_strength = abs(h1_ema_fast.iloc[-1] - h1_ema_slow.iloc[-1]) / max(1e-8, abs(h1_ema_slow.iloc[-1]))
        momentum_strength = abs(current_momentum) / max(1e-8, m5_atr)
        breakout_strength = abs(current_close - (recent_high if direction == 'BUY' else recent_low)) / max(1e-8, m5_atr)
        
        conf = 0.4 + 0.3 * min(1.0, trend_strength * 50.0) + 0.2 * min(1.0, momentum_strength) + 0.1 * min(1.0, breakout_strength)
        
        # Minimum confluence for risk premium trades
        min_confluence = float(p.get('min_confluence', 0.5))
        if conf < min_confluence:
            return None
        
        return {
            'strategy_name': 'momentum_premium_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"trend_strength={trend_strength:.4f} momentum={current_momentum:.4f} atr={m5_atr:.6f}",
        }
    except Exception:
        return None
