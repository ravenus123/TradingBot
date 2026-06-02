"""
Parabolic SAR Strategy
Trend-following indicator that places dots above/below price.
Price above SAR = UPTREND (BUY signals)
Price below SAR = DOWNTREND (SELL signals)
"""
import pandas as pd
import numpy as np

def calculate_parabolic_sar(df: pd.DataFrame, af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.2) -> pd.Series:
    """Calculate Parabolic SAR indicator."""
    high = df['High'].values
    low = df['Low'].values
    close = df['Close'].values
    
    n = len(df)
    sar = np.zeros(n)
    ep = np.zeros(n)  # Extreme point
    af = np.zeros(n)  # Acceleration factor
    
    # Initialize
    sar[0] = low[0]
    ep[0] = high[0]
    af[0] = af_start
    uptrend = True
    
    for i in range(1, n):
        if uptrend:
            sar[i] = sar[i-1] + af[i-1] * (ep[i-1] - sar[i-1])
            sar[i] = min(sar[i], low[i-1], low[i-2] if i >= 2 else low[i-1])
            
            if high[i] > ep[i-1]:
                ep[i] = high[i]
                af[i] = min(af[i-1] + af_step, af_max)
            else:
                ep[i] = ep[i-1]
                af[i] = af[i-1]
            
            if close[i] < sar[i]:
                uptrend = False
                sar[i] = ep[i]
                ep[i] = low[i]
                af[i] = af_start
        else:
            sar[i] = sar[i-1] + af[i-1] * (ep[i-1] - sar[i-1])
            sar[i] = max(sar[i], high[i-1], high[i-2] if i >= 2 else high[i-1])
            
            if low[i] < ep[i-1]:
                ep[i] = low[i]
                af[i] = min(af[i-1] + af_step, af_max)
            else:
                ep[i] = ep[i-1]
                af[i] = af[i-1]
            
            if close[i] > sar[i]:
                uptrend = True
                sar[i] = ep[i]
                ep[i] = high[i]
                af[i] = af_start
    
    return pd.Series(sar, index=df.index)

def generate_parabolic_sar_signal(df_h1: pd.DataFrame, df_m5: pd.DataFrame, symbol: str, sym_info: dict, params: dict) -> dict | None:
    """
    Generate signal based on Parabolic SAR trend direction.
    
    Params:
    - af_start: Starting acceleration factor (default 0.02)
    - af_step: Acceleration factor step (default 0.02)
    - af_max: Maximum acceleration factor (default 0.2)
    - use_sar_as_stop: Use SAR as trailing stop (default True)
    """
    af_start = params.get('af_start', 0.02)
    af_step = params.get('af_step', 0.02)
    af_max = params.get('af_max', 0.2)
    use_sar_as_stop = params.get('use_sar_as_stop', True)
    
    if len(df_m5) < 20:
        return None
    
    # Calculate Parabolic SAR
    sar = calculate_parabolic_sar(df_m5, af_start, af_step, af_max)
    
    current_price = df_m5['Close'].iloc[-1]
    current_sar = sar.iloc[-1]
    prev_sar = sar.iloc[-2]
    prev_price = df_m5['Close'].iloc[-2]
    
    # Check for trend change (signal)
    if prev_price > prev_sar and current_price < current_sar:
        # Trend changed from UP to DOWN - SELL signal
        direction = 'SELL'
    elif prev_price < prev_sar and current_price > current_sar:
        # Trend changed from DOWN to UP - BUY signal
        direction = 'BUY'
    else:
        # No trend change
        return None
    
    # Calculate entry, stop, target
    entry = current_price
    
    if use_sar_as_stop:
        stop = current_sar
    else:
        # ATR-based stop
        atr = df_m5['High'].iloc[-10:].max() - df_m5['Low'].iloc[-10:].min()
        stop_atr_mult = params.get('stop_atr_mult', 1.5)
        if direction == 'BUY':
            stop = entry - (atr * stop_atr_mult)
        else:
            stop = entry + (atr * stop_atr_mult)
    
    # ATR-based target
    atr = df_m5['High'].iloc[-10:].max() - df_m5['Low'].iloc[-10:].min()
    tp_atr_mult = params.get('tp_atr_mult', 2.0)
    
    if direction == 'BUY':
        target = entry + (atr * tp_atr_mult)
    else:
        target = entry - (atr * tp_atr_mult)
    
    
    return {
        'symbol': symbol,
        'direction': direction,
        'entry': entry,
        'stop': stop,
        'target': target,
        'strategy': 'parabolic_sar',
        'params': params,
        'rr': tp_atr_mult / stop_atr_mult if not use_sar_as_stop else 2.0,
        'score': abs(current_price - current_sar) / current_price * 100  # Distance from SAR as %
    }
