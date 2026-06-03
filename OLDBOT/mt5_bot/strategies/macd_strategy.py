"""MACD strategy.
Enters when MACD line crosses signal line at extreme levels.
Edge: Captures momentum changes from trend shifts with confirmation.
Who loses: Traders who enter without crossover confirmation or exit too early.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_macd_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    MACD strategy: enters when MACD line crosses signal line at extreme levels.
    Edge: Captures momentum changes from trend shifts with confirmation.
    Who loses: Traders who enter without crossover confirmation or exit too early.
    """
    try:
        p = params or {}
        
        # Use 5m data for MACD analysis
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        close = m5['Close'].astype(float)
        
        if len(close) < 50:
            return None
        
        # Parameters
        fast_period = int(p.get('fast_period', 12))
        slow_period = int(p.get('slow_period', 26))
        signal_period = int(p.get('signal_period', 9))
        
        # Calculate MACD
        ema_fast = close.ewm(span=fast_period, adjust=False).mean()
        ema_slow = close.ewm(span=slow_period, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
        histogram = macd_line - signal_line
        
        if len(macd_line) < signal_period + 5:
            return None
        
        # Current values
        macd_current = float(macd_line.iloc[-1])
        macd_prev = float(macd_line.iloc[-2])
        signal_current = float(signal_line.iloc[-1])
        signal_prev = float(signal_line.iloc[-2])
        hist_current = float(histogram.iloc[-1])
        hist_prev = float(histogram.iloc[-2])
        
        
        # Generate signals based on MACD crossovers
        # Bullish crossover: MACD crosses above signal line
        if macd_current > signal_current and macd_prev <= signal_prev:
            direction = 'BUY'
        # Bearish crossover: MACD crosses below signal line
        elif macd_current < signal_current and macd_prev >= signal_prev:
            direction = 'SELL'
        else:
            return None
        
        # Risk management
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(close.iloc[-1]) * 0.001)
        stop_mult = float(p.get('stop_atr_mult', 0.7))
        tp_mult = float(p.get('tp_atr_mult', 1.8))
        
        entry = float(close.iloc[-1])
        stop = entry - atr * stop_mult if direction == 'BUY' else entry + atr * stop_mult
        tp = entry + atr * tp_mult if direction == 'BUY' else entry - atr * tp_mult
        
        # Confluence based on MACD crossover strength and histogram momentum
        crossover_strength = min(1.0, abs(macd_current - signal_current) / max(1e-8, abs(signal_current)))
        hist_momentum = min(1.0, abs(hist_current - hist_prev) / max(1e-8, abs(atr * 0.1)))
        conf = 0.4 + 0.4 * crossover_strength + 0.2 * hist_momentum
        
        return {
            'strategy_name': 'macd_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"macd={macd_current:.6f} signal={signal_current:.6f} hist={hist_current:.6f}",
        }
    except Exception:
        return None
