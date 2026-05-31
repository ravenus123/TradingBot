"""Breakout strategy module.
Generates signals when price breaks out of recent range with volume confirmation.
Uncorrelated with mean reversion and trend momentum - exploits different market regime.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_breakout_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    Breakout strategy: enters when price breaks out of N-period range.
    Edge: Captures momentum when price breaks key levels, exploits stop-loss orders and breakout traders.
    Who loses: Traders with tight stops below/above range, breakout failures.
    """
    try:
        p = params or {}
        
        # Use 5m data for breakout detection
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        close = m5['Close'].astype(float)
        
        if len(close) < 50:
            return None
        
        # Parameters
        lookback = int(p.get('lookback', 20))  # Range lookback period
        breakout_threshold = float(p.get('breakout_threshold', 0.001))  # Minimum breakout size
        volume_confirm = bool(p.get('volume_confirm', True))
        
        # Calculate recent range
        recent_high = close.rolling(lookback).max().iloc[-1]
        recent_low = close.rolling(lookback).min().iloc[-1]
        current_price = close.iloc[-1]
        
        # Check for breakout
        breakout_up = current_price > recent_high * (1 + breakout_threshold)
        breakout_down = current_price < recent_low * (1 - breakout_threshold)
        
        if not (breakout_up or breakout_down):
            return None
        
        direction = 'BUY' if breakout_up else 'SELL'
        
        # Volume confirmation (if available)
        if volume_confirm and 'Volume' in m5.columns:
            vol_avg = m5['Volume'].rolling(lookback).mean().iloc[-1]
            vol_current = m5['Volume'].iloc[-1]
            if vol_current < vol_avg * 1.2:  # Need 20% above average volume
                return None
        
        # Risk management
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(current_price) * 0.001)
        stop_mult = float(p.get('stop_atr_mult', 1.0))
        tp_mult = float(p.get('tp_atr_mult', 2.0))
        
        entry = current_price
        stop = recent_low if direction == 'BUY' else recent_high  # Stop at opposite side of range
        tp = entry + atr * tp_mult if direction == 'BUY' else entry - atr * tp_mult
        
        # Confluence based on breakout strength
        breakout_strength = abs(current_price - (recent_high if breakout_up else recent_low)) / (recent_high - recent_low)
        conf = min(0.9, 0.5 + breakout_strength)
        
        return {
            'strategy_name': 'breakout_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': float(entry),
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"breakout range=[{recent_low:.5f}, {recent_high:.5f}] strength={breakout_strength:.3f}",
        }
    except Exception:
        return None
