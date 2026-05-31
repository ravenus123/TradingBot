"""
Volatility Breakout Strategy (Bollinger/Keltner Squeeze)
Institutional-grade volatility breakout that detects low-volatility compression
and enters on directional expansion with proper risk management.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def calculate_bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> tuple:
    """Calculate Bollinger Bands."""
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    return upper, middle, lower


def calculate_keltner_channels(high: pd.Series, low: pd.Series, close: pd.Series, 
                               period: int = 20, atr_mult: float = 2.0) -> tuple:
    """Calculate Keltner Channels."""
    atr = pd.Series(high - low).rolling(window=period).mean()
    middle = close.rolling(window=period).mean()
    upper = middle + (atr * atr_mult)
    lower = middle - (atr * atr_mult)
    return upper, middle, lower


def detect_squeeze(bb_upper: pd.Series, bb_lower: pd.Series, 
                  kc_upper: pd.Series, kc_lower: pd.Series) -> pd.Series:
    """
    Detect Bollinger/Keltner squeeze.
    Squeeze occurs when Bollinger Bands are inside Keltner Channels (low volatility).
    """
    squeeze = (bb_lower >= kc_lower) & (bb_upper <= kc_upper)
    return squeeze


def generate_volatility_breakout_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, 
                                       symbol: str, sym_info: dict, 
                                       params: dict | None = None) -> Optional[dict]:
    """
    Generate volatility breakout signal using Bollinger/Keltner squeeze.
    
    Strategy Logic:
    1. Detect squeeze (low volatility compression)
    2. Wait for squeeze release (expansion)
    3. Enter in direction of expansion
    4. Use ATR-based stops and targets
    """
    try:
        p = params or {}
        
        # Parameters
        bb_period = int(p.get('bb_period', 20))
        bb_std = float(p.get('bb_std', 2.0))
        kc_period = int(p.get('kc_period', 20))
        kc_mult = float(p.get('kc_mult', 1.5))
        squeeze_bars = int(p.get('squeeze_bars', 5))  # Minimum squeeze duration
        lookback = int(p.get('lookback', 10))
        
        m5 = df_5m.copy()
        if len(m5) < bb_period + lookback + 20:
            return None
        
        close = m5['Close'].astype(float)
        high = m5['High'].astype(float)
        low = m5['Low'].astype(float)
        
        # Calculate Bollinger Bands
        bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(close, bb_period, bb_std)
        
        # Calculate Keltner Channels
        kc_upper, kc_middle, kc_lower = calculate_keltner_channels(high, low, close, kc_period, kc_mult)
        
        # Detect squeeze
        squeeze = detect_squeeze(bb_upper, bb_lower, kc_upper, kc_lower)
        
        # Check for recent squeeze
        recent_squeeze = squeeze.iloc[-lookback:].sum()
        if recent_squeeze < squeeze_bars:
            return None  # No sufficient squeeze detected
        
        # Check for squeeze release (current bar not in squeeze)
        if squeeze.iloc[-1]:
            return None  # Still in squeeze, wait for release
        
        # Determine direction based on expansion
        # Compare current close to Bollinger Band position
        bb_width = (bb_upper - bb_lower) / bb_middle
        current_bb_width = bb_width.iloc[-1]
        avg_bb_width = bb_width.iloc[-lookback:].mean()
        
        if current_bb_width <= avg_bb_width * 1.2:
            return None  # No significant expansion yet
        
        # Direction: close above middle band = bullish, below = bearish
        last_close = close.iloc[-1]
        last_bb_middle = bb_middle.iloc[-1]
        
        if last_close > last_bb_middle:
            direction = 'BUY'
        elif last_close < last_bb_middle:
            direction = 'SELL'
        else:
            return None  # Neutral - no clear direction
        
        # Calculate ATR for stop sizing
        atr_series = (high - low).rolling(window=14).mean()
        atr = float(atr_series.iloc[-1])
        if not np.isfinite(atr) or atr <= 0:
            return None
        
        # Entry at current close (market order)
        entry = float(last_close)
        
        # Stop loss based on ATR
        stop_mult = float(p.get('stop_atr_mult', 1.5))
        if direction == 'BUY':
            stop = entry - (atr * stop_mult)
            # Ensure stop is below recent low
            recent_low = low.iloc[-lookback:].min()
            stop = min(stop, recent_low - (atr * 0.5))
        else:
            stop = entry + (atr * stop_mult)
            # Ensure stop is above recent high
            recent_high = high.iloc[-lookback:].max()
            stop = max(stop, recent_high + (atr * 0.5))
        
        # Take profit based on risk-reward ratio
        rr_ratio = float(p.get('rr_ratio', 2.0))
        stop_distance = abs(entry - stop)
        if direction == 'BUY':
            tp = entry + (stop_distance * rr_ratio)
        else:
            tp = entry - (stop_distance * rr_ratio)
        
        # Validate signal
        if stop_distance <= 0:
            return None
        
        # Calculate confluence score based on squeeze quality and expansion
        squeeze_quality = min(1.0, recent_squeeze / (squeeze_bars * 2))
        expansion_strength = min(1.0, (current_bb_width / avg_bb_width - 1.0) * 2)
        conf = 0.5 + (squeeze_quality * 0.3) + (expansion_strength * 0.2)
        
        return {
            'strategy_name': 'volatility_breakout_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': stop,
            'tp': tp,
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"vol_breakout squeeze_bars={recent_squeeze} exp={current_bb_width/avg_bb_width:.2f}x atr={atr:.6f}",
        }
        
    except Exception as e:
        print(f"[volatility_breakout] Error: {e}")
        return None
