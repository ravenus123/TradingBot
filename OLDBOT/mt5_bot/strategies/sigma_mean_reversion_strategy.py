"""
Sigma Mean Reversion Strategy
Enters when price drops below 3-sigma from rolling mean, expecting mean reversion.
Edge: Statistical mean reversion after extreme price moves.
Who loses: Traders who panic sell extreme drops without understanding statistical properties.
"""
import pandas as pd
import numpy as np

def generate_sigma_mean_reversion_signal(df_h1: pd.DataFrame, df_m5: pd.DataFrame, symbol: str, sym_info: dict, params: dict) -> dict | None:
    """
    Generate signal based on sigma mean reversion.
    
    Params:
    - window: Rolling window for mean/std calculation (default 100)
    - sigma_threshold: Sigma threshold for entry (default 3.0)
    - lookforward_period: Periods to hold position (default 12)
    - stop_atr_mult: ATR multiplier for stop loss (default 1.5)
    - tp_atr_mult: ATR multiplier for take profit (default 2.0)
    """
    window = params.get('window', 100)
    sigma_threshold = params.get('sigma_threshold', 3.0)
    lookforward_period = params.get('lookforward_period', 12)
    stop_atr_mult = params.get('stop_atr_mult', 1.5)
    tp_atr_mult = params.get('tp_atr_mult', 2.0)
    
    if len(df_m5) < window + 10:
        return None
    
    # Calculate rolling mean and std
    close = df_m5['close']
    rolling_mean = close.rolling(window).mean()
    rolling_std = close.rolling(window).std()
    
    # Calculate z-score
    z_score = (close - rolling_mean) / rolling_std
    
    current_z_score = z_score.iloc[-1]
    
    # Only enter on extreme drops (below -sigma_threshold)
    if current_z_score > -sigma_threshold:
        return None
    
    direction = 'BUY'  # Mean reversion after drop
    
    # Calculate entry, stop, target
    entry = close.iloc[-1]
    
    # ATR-based stop
    atr = df_m5['high'].iloc[-10:].max() - df_m5['low'].iloc[-10:].min()
    
    stop = entry - (atr * stop_atr_mult)
    target = entry + (atr * tp_atr_mult)
    
    return {
        'symbol': symbol,
        'direction': direction,
        'entry': entry,
        'stop': stop,
        'target': target,
        'strategy': 'sigma_mean_reversion',
        'params': params,
        'rr': tp_atr_mult / stop_atr_mult,
        'score': abs(current_z_score)  # Higher absolute z-score = stronger signal
    }
