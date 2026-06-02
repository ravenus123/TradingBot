"""
CCI (Commodity Channel Index) Strategy
CCI measures deviation from typical price. Used for overbought/oversold and divergence.
CCI > 100: Overbought (potential SELL)
CCI < -100: Oversold (potential BUY)
"""
import pandas as pd
import numpy as np

def calculate_cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Calculate CCI indicator."""
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    sma_tp = typical_price.rolling(window=period).mean()
    mad = typical_price.rolling(window=period).apply(lambda x: np.mean(np.abs(x - x.mean())))
    cci = (typical_price - sma_tp) / (0.015 * mad)
    return cci

def generate_cci_signal(df_h1: pd.DataFrame, df_m5: pd.DataFrame, symbol: str, sym_info: dict, params: dict) -> dict | None:
    """
    Generate signal based on CCI overbought/oversold levels.
    
    Params:
    - cci_period: CCI calculation period (default 20)
    - overbought: Overbought threshold (default 100)
    - oversold: Oversold threshold (default -100)
    - use_divergence: Use divergence detection (default False)
    """
    cci_period = params.get('cci_period', 20)
    overbought = params.get('overbought', 100)
    oversold = params.get('oversold', -100)
    use_divergence = params.get('use_divergence', False)
    
    if len(df_m5) < cci_period + 10:
        return None
    
    # Calculate CCI
    cci = calculate_cci(df_m5, cci_period)
    current_cci = cci.iloc[-1]
    prev_cci = cci.iloc[-2]
    
    # Check for overbought/oversold signals
    if current_cci > overbought:
        # Overbought - potential SELL
        # Wait for CCI to cross back below overbought
        if prev_cci > overbought and current_cci < overbought:
            direction = 'SELL'
        else:
            return None
    elif current_cci < oversold:
        # Oversold - potential BUY
        # Wait for CCI to cross back above oversold
        if prev_cci < oversold and current_cci > oversold:
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
        'strategy': 'cci',
        'params': params,
        'rr': tp_atr_mult / stop_atr_mult,
        'score': abs(current_cci)  # Higher absolute CCI = stronger extreme
    }
