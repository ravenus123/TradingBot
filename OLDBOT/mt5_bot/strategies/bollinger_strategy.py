"""Bollinger Bands strategy.
Enters when price touches or exceeds Bollinger Bands, expecting mean reversion.
Edge: Captures mean reversion from overextended price moves beyond statistical extremes.
Who loses: Traders who chase breakouts without understanding mean reversion dynamics.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_bollinger_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    Bollinger Bands strategy: enters when price touches or exceeds Bollinger Bands.
    Edge: Captures mean reversion from overextended price moves beyond statistical extremes.
    Who loses: Traders who chase breakouts without understanding mean reversion dynamics.
    """
    try:
        p = params or {}
        
        # Use 5m data for Bollinger analysis
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        close = m5['Close'].astype(float)
        
        if len(close) < 50:
            return None
        
        # Parameters
        period = int(p.get('period', 20))
        std_dev = float(p.get('std_dev', 2.0))
        
        # Calculate Bollinger Bands
        sma = close.rolling(window=period).mean()
        std = close.rolling(window=period).std()
        upper_band = sma + std_dev * std
        lower_band = sma - std_dev * std
        
        if len(upper_band) < period + 5:
            return None
        
        # Current values
        current_close = float(close.iloc[-1])
        current_upper = float(upper_band.iloc[-1])
        current_lower = float(lower_band.iloc[-1])
        current_sma = float(sma.iloc[-1])
        
        # Calculate %B (position within bands)
        band_width = current_upper - current_lower
        percent_b = (current_close - current_lower) / max(1e-8, band_width)
        
        
        # Generate signals based on Bollinger Band touches
        # Price at or above upper band - expect mean reversion down
        if percent_b >= 0.95:
            direction = 'SELL'
        # Price at or below lower band - expect mean reversion up
        elif percent_b <= 0.05:
            direction = 'BUY'
        else:
            return None
        
        # Risk management
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(current_close) * 0.001)
        stop_mult = float(p.get('stop_atr_mult', 0.6))
        tp_mult = float(p.get('tp_atr_mult', 1.5))
        
        entry = current_close
        stop = current_upper if direction == 'SELL' else current_lower
        stop = stop + atr * stop_mult if direction == 'SELL' else stop - atr * stop_mult
        tp = current_sma if direction == 'SELL' else current_sma
        tp = tp - atr * tp_mult if direction == 'SELL' else tp + atr * tp_mult
        
        # Confluence based on band extremeness and distance from mean
        band_extremeness = min(1.0, abs(percent_b - 0.5) / 0.45)
        distance_from_mean = abs(current_close - current_sma) / max(1e-8, atr)
        mean_reversion_strength = min(1.0, distance_from_mean / 2.0)
        conf = 0.4 + 0.4 * band_extremeness + 0.2 * mean_reversion_strength
        
        return {
            'strategy_name': 'bollinger_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"percent_b={percent_b:.2f} upper={current_upper:.6f} lower={current_lower:.6f}",
        }
    except Exception:
        return None
