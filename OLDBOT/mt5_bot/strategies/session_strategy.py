"""
Session-Based Trading Strategy

EDGE EXPLANATION (5 minutes):
==============================
WHY IT MAKES MONEY:
- Different trading sessions have different characteristics and participants
- London session: European institutional flow, often trends
- New York session: US institutions, often volatile with news events
- Asian session: Thinner liquidity, often range-bound
- Session overlaps: Highest volatility and volume (London/NY overlap)
- Trading specific session characteristics provides statistical edge

WHO'S ON THE LOSING SIDE:
- Traders applying the same strategy across all sessions without adaptation
- Traders not aware that market behavior changes by session
- Retail traders trading during low-probability times (e.g., Asian session for EURUSD)

WHAT THEY'RE DOING WRONG:
- Not adjusting strategy for session-specific characteristics
- Trading during low-liquidity times with wide spreads
- Failing to recognize that the same setup has different probabilities by time of day

STRATEGY LOGIC:
===============
1. Determine current trading session
2. Apply session-specific entry criteria
3. Adjust risk parameters based on session volatility
4. Focus on high-probability session setups
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def get_trading_session(dt: datetime) -> str:
    """Determine trading session based on UTC time."""
    hour = dt.hour
    
    # Asian session (00:00 - 06:00 UTC)
    if 0 <= hour < 6:
        return 'asian'
    
    # London session (07:00 - 15:00 UTC) 
    elif 7 <= hour < 15:
        return 'london'
    
    # New York session (13:00 - 21:00 UTC)
    elif 13 <= hour < 21:
        return 'new_york'
    
    # London/New York overlap (13:00 - 15:00 UTC) - highest volatility
    elif 13 <= hour < 15:
        return 'overlap'
    
    # Off-hours
    else:
        return 'off_hours'


def generate_session_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    Session-based strategy: Trades specific session characteristics.
    
    Edge: Different sessions have different characteristics and probabilities.
    Who loses: Traders not adapting to session-specific behavior.
    """
    try:
        p = params or {}
        
        # Use 5m data for precise timing
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        close = m5['Close'].astype(float)
        high = m5['High'].astype(float)
        low = m5['Low'].astype(float)
        
        if len(close) < 50:
            return None
        
        # Get current session
        current_time = datetime.utcnow()
        session = get_trading_session(current_time)
        
        # Only trade during high-probability sessions (relaxed for testing)
        preferred_sessions = p.get('preferred_sessions', ['london', 'new_york', 'overlap', 'asian'])
        if session not in preferred_sessions:
            return None
        
        # Session-specific parameters
        if session == 'overlap':
            # Highest volatility - breakout strategy
            breakout_window = int(p.get('breakout_window', 12))  # 1 hour in 5m bars
            breakout_threshold = float(p.get('breakout_threshold', 0.5))
            stop_atr_mult = float(p.get('stop_atr_mult', 0.8))
            tp_atr_mult = float(p.get('tp_atr_mult', 2.0))
            
        elif session == 'london':
            # Trend following
            breakout_window = int(p.get('breakout_window', 24))  # 2 hours
            breakout_threshold = float(p.get('breakout_threshold', 0.3))
            stop_atr_mult = float(p.get('stop_atr_mult', 1.0))
            tp_atr_mult = float(p.get('tp_atr_mult', 2.5))
            
        elif session == 'new_york':
            # Momentum with reversals
            breakout_window = int(p.get('breakout_window', 18))
            breakout_threshold = float(p.get('breakout_threshold', 0.4))
            stop_atr_mult = float(p.get('stop_atr_mult', 0.9))
            tp_atr_mult = float(p.get('tp_atr_mult', 2.2))
            
        else:
            return None
        
        # Calculate ATR
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(close.iloc[-1]) * 0.001)
        
        # Calculate recent range
        recent_high = high.rolling(window=breakout_window).max().iloc[-2]
        recent_low = low.rolling(window=breakout_window).min().iloc[-2]
        current_close = float(close.iloc[-1])
        
        # Breakout logic
        breakout_threshold_atr = breakout_threshold * atr
        
        direction = None
        if current_close > recent_high + breakout_threshold_atr:
            direction = 'BUY'
        elif current_close < recent_low - breakout_threshold_atr:
            direction = 'SELL'
        else:
            return None
        
        # Volume confirmation (more important in certain sessions)
        volume_confirmation = p.get('volume_confirmation', True)
        if volume_confirmation and 'Volume' in m5.columns:
            avg_volume = m5['Volume'].rolling(window=breakout_window).mean().iloc[-2]
            if m5['Volume'].iloc[-1] < avg_volume * 1.1:
                return None
        
        # Calculate risk management
        entry = current_close
        stop = entry - atr * stop_atr_mult if direction == 'BUY' else entry + atr * stop_atr_mult
        tp = entry + atr * tp_atr_mult if direction == 'BUY' else entry - atr * tp_atr_mult
        
        # Session-specific confluence
        if session == 'overlap':
            # Higher confluence for overlap (best session)
            conf = 0.6 + 0.2 * min(1.0, abs(current_close - (recent_high if direction == 'BUY' else recent_low)) / (atr * 2.0))
        elif session == 'london':
            conf = 0.5 + 0.2 * min(1.0, abs(current_close - (recent_high if direction == 'BUY' else recent_low)) / (atr * 2.0))
        else:  # new_york
            conf = 0.5 + 0.2 * min(1.0, abs(current_close - (recent_high if direction == 'BUY' else recent_low)) / (atr * 2.0))
        
        # Session-specific minimum confluence
        min_confluence = float(p.get('min_confluence', 0.6))
        if conf < min_confluence:
            return None
        
        return {
            'strategy_name': 'session_strategy_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"session={session} breakout_strength={abs(current_close - (recent_high if direction == 'BUY' else recent_low))/atr:.2f}",
        }
    except Exception:
        return None
