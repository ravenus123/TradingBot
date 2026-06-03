"""Breakout strategy.
Enters when price breaks out of a recent range with volume confirmation.
Edge: Captures momentum from institutional order flow pushing price through key levels.
Who loses: Traders who fade breakouts without understanding the underlying institutional urgency.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_breakout_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    Breakout strategy: enters when price breaks out of recent range with volume confirmation.
    Edge: Captures momentum from institutional order flow pushing price through key levels.
    Who loses: Traders who fade breakouts without understanding institutional urgency.
    """
    try:
        p = params or {}
        
        # Use 5m data for breakout analysis
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        close = m5['Close'].astype(float)
        high = m5['High'].astype(float)
        low = m5['Low'].astype(float)
        volume = m5['Volume'].astype(float) if 'Volume' in m5.columns else pd.Series([1] * len(m5))
        
        if len(close) < 50:
            return None
        
        # Parameters
        lookback_period = int(p.get('lookback_period', 24))
        breakout_threshold = float(p.get('breakout_threshold', 0.5))
        volume_confirmation = p.get('volume_confirmation', True)
        
        # Calculate recent range
        recent_high = high.rolling(window=lookback_period).max()
        recent_low = low.rolling(window=lookback_period).min()
        range_size = recent_high - recent_low
        
        # Calculate average volume
        avg_volume = volume.rolling(window=lookback_period).mean()
        
        if len(recent_high) < lookback_period + 5:
            return None
        
        # Current values
        current_close = float(close.iloc[-1])
        current_high = float(recent_high.iloc[-2])
        current_low = float(recent_low.iloc[-2])
        current_range = float(range_size.iloc[-2])
        current_volume = float(volume.iloc[-1])
        avg_vol_current = float(avg_volume.iloc[-1])
        
        # Generate signals based on breakout
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(current_close) * 0.001)
        breakout_threshold_atr = breakout_threshold * atr
        
        direction = None
        if current_close > current_high + breakout_threshold_atr:
            direction = 'BUY'
        elif current_close < current_low - breakout_threshold_atr:
            direction = 'SELL'
        else:
            return None
        
        # Volume confirmation
        if volume_confirmation and current_volume < avg_vol_current * 1.2:
            return None
        
        # Risk management
        stop_mult = float(p.get('stop_atr_mult', 0.8))
        tp_mult = float(p.get('tp_atr_mult', 2.0))
        
        entry = current_close
        stop = current_high if direction == 'SELL' else current_low
        stop = stop - atr * stop_mult * 0.5 if direction == 'BUY' else stop + atr * stop_mult * 0.5
        tp = entry + atr * tp_mult if direction == 'BUY' else entry - atr * tp_mult
        
        # Confluence based on breakout strength and volume
        breakout_strength = min(1.0, abs(current_close - (current_high if direction == 'BUY' else current_low)) / max(1e-8, atr))
        volume_ratio = current_volume / max(1e-8, avg_vol_current)
        volume_conf = min(1.0, (volume_ratio - 1.0) / 0.5) if volume_confirmation else 0.5
        conf = 0.4 + 0.4 * breakout_strength + 0.2 * volume_conf
        
        return {
            'strategy_name': 'breakout_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"breakout_strength={breakout_strength:.2f} vol_ratio={volume_ratio:.2f} atr={atr:.6f}",
        }
    except Exception:
        return None
