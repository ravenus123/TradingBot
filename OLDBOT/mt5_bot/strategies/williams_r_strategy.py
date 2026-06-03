"""
Williams %R Strategy
Momentum indicator similar to Stochastic but ranges from -100 to 0.
-80 to -100: Oversold (potential BUY)
-0 to -20: Overbought (potential SELL)
"""
import pandas as pd
import numpy as np

def calculate_williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Williams %R indicator."""
    high_max = df['High'].rolling(window=period).max()
    low_min = df['Low'].rolling(window=period).min()
    williams_r = -100 * (high_max - df['Close']) / (high_max - low_min)
    return williams_r

def generate_williams_r_signal(df_h1: pd.DataFrame, df_m5: pd.DataFrame, symbol: str, sym_info: dict, params: dict) -> dict | None:
    """
    Generate signal based on Williams %R overbought/oversold levels.
    
    Params:
    - period: Williams %R period (default 14)
    - overbought: Overbought threshold (default -20)
    - oversold: Oversold threshold (default -80)
    """
    period = params.get('period', 14)
    overbought = params.get('overbought', -20)
    oversold = params.get('oversold', -80)
    
    if len(df_m5) < period + 10:
        return None
    
    # Calculate Williams %R
    williams_r = calculate_williams_r(df_m5, period)
    current_wr = williams_r.iloc[-1]
    prev_wr = williams_r.iloc[-2]
    
    # Check for overbought/oversold signals
    if current_wr > overbought:
        # Overbought - potential SELL
        # Wait for crossover back below overbought
        if prev_wr > overbought and current_wr < overbought:
            direction = 'SELL'
        else:
            return None
    elif current_wr < oversold:
        # Oversold - potential BUY
        # Wait for crossover back above oversold
        if prev_wr < oversold and current_wr > oversold:
            direction = 'BUY'
        else:
            return None
    else:
        return None
    
    # Calculate entry, stop, target
    entry = df_m5['Close'].iloc[-1]
    
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
        'strategy': 'williams_r',
        'params': params,
        'rr': tp_atr_mult / stop_atr_mult,
        'score': abs(current_wr + 50)  # Distance from midpoint
    }
