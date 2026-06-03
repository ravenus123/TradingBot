"""
Multi-Timeframe Confluence Strategy
Uses H1 trend direction (EMA) and M15 entries (pullback to EMA).
Edge: Trades with the trend on higher timeframe, enters on lower timeframe pullbacks.
Who loses: Traders who fight the higher timeframe trend or chase entries without confluence.
"""
import pandas as pd
import numpy as np

def generate_multi_timeframe_signal(df_h1: pd.DataFrame, df_m5: pd.DataFrame, symbol: str, sym_info: dict, params: dict) -> dict | None:
    """
    Generate signal based on H1 trend + M15 pullback confluence.
    
    Params:
    - h1_ema_period: H1 EMA period for trend (default 50)
    - m15_ema_period: M15 EMA period for pullback (default 20)
    - pullback_pct: Pullback percentage to enter (default 0.5%)
    """
    h1_ema_period = params.get('h1_ema_period', 50)
    m15_ema_period = params.get('m15_ema_period', 20)
    pullback_pct = params.get('pullback_pct', 0.5)
    
    if len(df_h1) < h1_ema_period + 10 or len(df_m5) < m15_ema_period + 10:
        return None
    
    # H1 trend direction
    h1_close = df_h1['Close']
    h1_ema = h1_close.ewm(span=h1_ema_period, adjust=False).mean()
    h1_trend = 'UP' if h1_close.iloc[-1] > h1_ema.iloc[-1] else 'DOWN'
    
    # M15 pullback detection
    m15_close = df_m5['Close']
    m15_ema = m15_close.ewm(span=m15_ema_period, adjust=False).mean()
    m15_current_close = m15_close.iloc[-1]
    m15_ema_current = m15_ema.iloc[-1]
    
    # Calculate pullback percentage
    if h1_trend == 'UP':
        # In uptrend, look for pullback below M15 EMA
        pullback = (m15_ema_current - m15_current_close) / m15_ema_current * 100
        if pullback > pullback_pct and m15_current_close > m15_ema.iloc[-5]:  # Pullback but still above recent EMA
            direction = 'BUY'
        else:
            return None
    else:
        # In downtrend, look for pullback above M15 EMA
        pullback = (m15_current_close - m15_ema_current) / m15_ema_current * 100
        if pullback > pullback_pct and m15_current_close < m15_ema.iloc[-5]:  # Pullback but still below recent EMA
            direction = 'SELL'
        else:
            return None
    
    # Calculate entry, stop, target
    entry = m15_current_close
    
    # ATR-based stop
    atr = df_m5['High'].iloc[-10:].max() - df_m5['Low'].iloc[-10:].min()
    stop_atr_mult = params.get('stop_atr_mult', 1.5)
    tp_atr_mult = params.get('tp_atr_mult', 2.0)
    
    if direction == 'BUY':
        stop = entry - (atr * stop_atr_mult)
        target = entry + (atr * tp_atr_mult)
    else:
        stop = entry + (atr * stop_atr_mult)
        target = entry - (atr * tp_atr_mult)
    
    return {
        'symbol': symbol,
        'direction': direction,
        'entry': entry,
        'stop': stop,
        'target': target,
        'strategy': 'multi_timeframe',
        'params': params,
        'rr': tp_atr_mult / stop_atr_mult,
        'score': pullback  # Higher pullback = better entry
    }
