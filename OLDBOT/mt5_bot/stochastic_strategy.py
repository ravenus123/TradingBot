"""Stochastic oscillator-based strategy module.
Generates signals based on Stochastic oscillator crossovers and extreme levels.
Uncorrelated with RSI and mean reversion - exploits momentum and overbought/oversold conditions.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_stochastic_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    Stochastic strategy: enters when %K crosses %D at extreme levels.
    Edge: Captures momentum changes at overbought/oversold extremes with confirmation.
    Who loses: Traders who enter without crossover confirmation or exit too early.
    """
    try:
        p = params or {}
        
        # Use 5m data for Stochastic analysis
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        close = m5['Close'].astype(float)
        high = m5['High'].astype(float)
        low = m5['Low'].astype(float)
        
        if len(close) < 50:
            return None
        
        # Parameters
        k_period = int(p.get('k_period', 14))
        d_period = int(p.get('d_period', 3))
        stoch_oversold = float(p.get('stoch_oversold', 20.0))
        stoch_overbought = float(p.get('stoch_overbought', 80.0))
        
        # Calculate Stochastic oscillator
        low_min = low.rolling(window=k_period).min()
        high_max = high.rolling(window=k_period).max()
        
        # Avoid division by zero
        range_val = high_max - low_min
        range_val = range_val.replace(0, np.nan)
        
        k_percent = 100 * ((close - low_min) / range_val)
        k_percent = k_percent.fillna(50)  # Fill NaN with neutral
        
        d_percent = k_percent.rolling(window=d_period).mean()
        
        if len(k_percent) < 5:
            return None
        
        # Current values
        k_current = float(k_percent.iloc[-1])
        k_prev = float(k_percent.iloc[-2])
        d_current = float(d_percent.iloc[-1])
        d_prev = float(d_percent.iloc[-2])
        
        # Generate signals based on Stochastic crossovers at extremes
        # Bullish crossover: %K crosses above %D in oversold territory
        if k_current > d_current and k_prev <= d_prev and k_current < stoch_oversold + 10:
            direction = 'BUY'
        # Bearish crossover: %K crosses below %D in overbought territory
        elif k_current < d_current and k_prev >= d_prev and k_current > stoch_overbought - 10:
            direction = 'SELL'
        else:
            return None
        
        # Risk management
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(close.iloc[-1]) * 0.001)
        stop_mult = float(p.get('stop_atr_mult', 1.0))
        tp_mult = float(p.get('tp_atr_mult', 2.0))
        
        entry = float(close.iloc[-1])
        stop = entry - atr * stop_mult if direction == 'BUY' else entry + atr * stop_mult
        tp = entry + atr * tp_mult if direction == 'BUY' else entry - atr * tp_mult
        
        # Confluence based on Stochastic extremeness and crossover strength
        stoch_extremeness = min(1.0, (abs(k_current - 50) / 30.0))  # 0 to 1 based on how far from 50
        crossover_strength = min(1.0, abs(k_current - d_current) / 5.0)  # 0 to 1 based on crossover gap
        conf = 0.4 + 0.4 * stoch_extremeness + 0.2 * crossover_strength
        
        return {
            'strategy_name': 'stochastic_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"K={k_current:.2f} D={d_current:.2f} oversold={stoch_oversold} overbought={stoch_overbought}",
        }
    except Exception:
        return None
