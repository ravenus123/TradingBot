"""
Price Action Pattern Strategy
Detects candlestick patterns: engulfing, doji, hammer, shooting star.
"""
import pandas as pd
import numpy as np

def detect_engulfing(df: pd.DataFrame, i: int) -> str | None:
    """Detect bullish or bearish engulfing pattern at index i."""
    if i < 1:
        return None
    
    curr = df.iloc[i]
    prev = df.iloc[i-1]
    
    curr_body = abs(curr['Close'] - curr['Open'])
    prev_body = abs(prev['Close'] - prev['Open'])
    
    # Bullish engulfing: current green candle engulfs previous red candle
    if curr['Close'] > curr['Open'] and prev['Close'] < prev['Open']:
        if curr['Open'] <= prev['Close'] and curr['Close'] >= prev['Open']:
            if curr_body > prev_body:
                return 'BUY'
    
    # Bearish engulfing: current red candle engulfs previous green candle
    if curr['Close'] < curr['Open'] and prev['Close'] > prev['Open']:
        if curr['Open'] >= prev['Close'] and curr['Close'] <= prev['Open']:
            if curr_body > prev_body:
                return 'SELL'
    
    return None

def detect_doji(df: pd.DataFrame, i: int) -> str | None:
    """Detect doji pattern at index i (very small body)."""
    if i < 0:
        return None
    
    curr = df.iloc[i]
    body = abs(curr['Close'] - curr['Open'])
    range_size = curr['High'] - curr['Low']
    
    # Doji: body is very small relative to range (< 10%)
    if range_size > 0 and body / range_size < 0.1:
        # Determine direction based on next candle
        if i + 1 < len(df):
            next_candle = df.iloc[i+1]
            if next_candle['Close'] > next_candle['Open']:
                return 'BUY'
            elif next_candle['Close'] < next_candle['Open']:
                return 'SELL'
    
    return None

def detect_hammer(df: pd.DataFrame, i: int) -> str | None:
    """Detect hammer (bullish) or shooting star (bearish) pattern at index i."""
    if i < 1:
        return None
    
    curr = df.iloc[i]
    prev = df.iloc[i-1]
    
    body = abs(curr['Close'] - curr['Open'])
    upper_wick = curr['High'] - max(curr['Open'], curr['Close'])
    lower_wick = min(curr['Open'], curr['Close']) - curr['Low']
    range_size = curr['High'] - curr['Low']
    
    if range_size == 0:
        return None
    
    # Hammer: small body at top, long lower wick (at least 2x body), small upper wick
    if lower_wick > 2 * body and upper_wick < body:
        # Check if in downtrend
        if prev['Close'] < prev['Open']:
            return 'BUY'
    
    # Shooting star: small body at bottom, long upper wick (at least 2x body), small lower wick
    if upper_wick > 2 * body and lower_wick < body:
        # Check if in uptrend
        if prev['Close'] > prev['Open']:
            return 'SELL'
    
    return None

def generate_price_action_signal(df_h1: pd.DataFrame, df_m5: pd.DataFrame, symbol: str, sym_info: dict, params: dict) -> dict | None:
    """
    Generate signal based on price action patterns.
    
    Params:
    - use_engulfing: Use engulfing patterns (default True)
    - use_doji: Use doji patterns (default True)
    - use_hammer: Use hammer/shooting star patterns (default True)
    """
    use_engulfing = params.get('use_engulfing', True)
    use_doji = params.get('use_doji', True)
    use_hammer = params.get('use_hammer', True)
    
    if len(df_m5) < 5:
        return None
    
    # Check recent candles for patterns
    direction = None
    pattern_name = None
    
    for i in range(min(5, len(df_m5) - 1), max(0, len(df_m5) - 6), -1):
        if use_engulfing:
            engulfing = detect_engulfing(df_m5, i)
            if engulfing:
                direction = engulfing
                pattern_name = 'engulfing'
                break
        
        if use_doji:
            doji = detect_doji(df_m5, i)
            if doji:
                direction = doji
                pattern_name = 'doji'
                break
        
        if use_hammer:
            hammer = detect_hammer(df_m5, i)
            if hammer:
                direction = hammer
                pattern_name = 'hammer' if hammer == 'BUY' else 'shooting_star'
                break
    
    if direction is None:
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
        'strategy': 'price_action',
        'params': params,
        'rr': tp_atr_mult / stop_atr_mult,
        'score': 1.0  # All patterns have equal score
    }
