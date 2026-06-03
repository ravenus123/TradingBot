"""
Candle Reversal Strategy
Enters after large bearish candles, expecting reversal.
Edge: Overreaction to large moves creates mean reversion opportunities.
Who loses: Traders who chase large moves without understanding mean reversion.
"""
import pandas as pd
import numpy as np

def generate_candle_reversal_signal(df_h1: pd.DataFrame, df_m5: pd.DataFrame, symbol: str, sym_info: dict, params: dict) -> dict | None:
    """
    Generate signal based on candle reversal patterns.
    
    Params:
    - body_multiplier: Multiplier for average body to identify large candles (default 2.0)
    - lookforward_period: Periods to hold position (default 4)
    - stop_atr_mult: ATR multiplier for stop loss (default 1.5)
    - tp_atr_mult: ATR multiplier for take profit (default 2.0)
    """
    body_multiplier = params.get('body_multiplier', 2.0)
    lookforward_period = params.get('lookforward_period', 4)
    stop_atr_mult = params.get('stop_atr_mult', 1.5)
    tp_atr_mult = params.get('tp_atr_mult', 2.0)
    
    if len(df_m5) < 100:
        return None
    
    # Calculate candle body and range
    df = df_m5.copy()
    df['body'] = abs(df['close'] - df['open'])
    df['range'] = df['high'] - df['low']
    
    # Calculate average body over last 100 candles
    avg_body = df['body'].iloc[-100:].mean()
    
    # Identify large bearish candles (body > multiplier * avg body and close < open)
    current_body = df['body'].iloc[-1]
    current_close = df['close'].iloc[-1]
    current_open = df['open'].iloc[-1]
    
    is_large_bearish = (current_body > body_multiplier * avg_body) and (current_close < current_open)
    
    if not is_large_bearish:
        return None
    
    direction = 'BUY'  # Reversal after large bearish candle
    
    # Calculate entry, stop, target
    entry = current_close
    
    # ATR-based stop
    atr = df['high'].iloc[-10:].max() - df['low'].iloc[-10:].min()
    
    stop = entry - (atr * stop_atr_mult)
    target = entry + (atr * tp_atr_mult)
    
    return {
        'symbol': symbol,
        'direction': direction,
        'entry': entry,
        'stop': stop,
        'target': target,
        'strategy': 'candle_reversal',
        'params': params,
        'rr': tp_atr_mult / stop_atr_mult,
        'score': current_body / avg_body  # Higher body ratio = stronger signal
    }
