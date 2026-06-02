"""
ADX Trend Strength Strategy
Uses ADX to identify strong trends and combines with directional indicators for entry.
ADX > 25: Strong trend - trade in direction
ADX < 20: Weak/ranging - avoid trading
"""
import pandas as pd
import numpy as np

def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Calculate ADX indicator."""
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    # True Range
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Directional Movement
    plus_dm = high - high.shift()
    minus_dm = low.shift() - low
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    
    # Smoothed TR and DM
    atr = tr.rolling(window=period).mean()
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
    
    # DX and ADX
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.rolling(window=period).mean()
    
    return pd.DataFrame({
        'ADX': adx,
        'Plus_DI': plus_di,
        'Minus_DI': minus_di
    })

def generate_adx_signal(df_h1: pd.DataFrame, df_m5: pd.DataFrame, symbol: str, sym_info: dict, params: dict) -> dict | None:
    """
    Generate signal based on ADX trend strength + directional bias.
    
    Params:
    - adx_period: ADX calculation period (default 14)
    - adx_threshold: Minimum ADX for strong trend (default 25)
    - adx_weak_threshold: Maximum ADX for weak trend (default 20)
    - use_di: Use DI for direction (default True)
    """
    adx_period = params.get('adx_period', 14)
    adx_threshold = params.get('adx_threshold', 25)
    adx_weak_threshold = params.get('adx_weak_threshold', 20)
    use_di = params.get('use_di', True)
    
    if len(df_m5) < adx_period + 10:
        return None
    
    # Calculate ADX on M5
    adx_data = calculate_adx(df_m5, adx_period)
    adx = adx_data['ADX'].iloc[-1]
    plus_di = adx_data['Plus_DI'].iloc[-1]
    minus_di = adx_data['Minus_DI'].iloc[-1]
    
    # Skip if ADX is too low (weak/ranging market)
    if adx < adx_weak_threshold:
        return None
    
    # Skip if ADX is not strong enough
    if adx < adx_threshold:
        return None
    
    # Determine direction
    if use_di:
        # Use DI crossover for direction
        if plus_di > minus_di:
            direction = 'BUY'
        else:
            direction = 'SELL'
    else:
        # Use price trend for direction
        if df_m5['Close'].iloc[-1] > df_m5['Close'].iloc[-5]:
            direction = 'BUY'
        else:
            direction = 'SELL'
    
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
        'strategy': 'adx',
        'params': params,
        'rr': tp_atr_mult / stop_atr_mult,
        'score': adx  # Higher ADX = stronger trend = higher score
    }
