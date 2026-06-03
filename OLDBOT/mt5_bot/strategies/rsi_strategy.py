"""RSI-based strategy module.
Generates signals based on RSI overbought/oversold conditions.
Simple, robust strategy that should generate consistent signals across market conditions.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_rsi_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    RSI strategy: enters when RSI is overbought (>75) for sells or oversold (<25) for buys with trend filter.
    Edge: Captures mean reversion when price is at extreme levels, avoiding counter-trend trades.
    Who loses: Traders who chase momentum at extremes without risk management.
    """
    try:
        p = params or {}
        
        # Use 5m data for RSI analysis
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        h1 = add_indicators(df_1h, ema_period=34).fillna(0)
        close = m5['Close'].astype(float)
        
        if len(close) < 50:
            return None
        
        # Parameters - more extreme thresholds for better edge
        rsi_period = int(p.get('rsi_period', 14))
        rsi_oversold = float(p.get('rsi_oversold', 25.0))  # More extreme: 25 instead of 30
        rsi_overbought = float(p.get('rsi_overbought', 75.0))  # More extreme: 75 instead of 70
        
        # Get RSI
        rsi = float(m5['RSI'].iloc[-1]) if 'RSI' in m5.columns else 50.0
        
        # Trend filter using H1 EMA - avoid mean reversion in strong trends
        ema_h1 = h1['EMA'].astype(float).values
        ema_slope = ema_h1[-1] - ema_h1[-5] if len(ema_h1) > 5 else 0.0
        trend_strength = abs(ema_slope) / max(1e-6, abs(ema_h1[-1]))
        
        # Only trade if trend is not too strong (avoid counter-trend in strong moves)
        max_trend_strength = float(p.get('max_trend_strength', 0.005))
        if trend_strength > max_trend_strength:
            return None

        # Regime filter via ADX: skip if market strongly trending
        adx = float(m5['ADX'].iloc[-1]) if 'ADX' in m5.columns else 0.0
        max_adx = float(p.get('max_adx', 25.0))
        if adx > max_adx:
            return None
        
        # Generate signals based on RSI extremes
        if rsi < rsi_oversold:
            direction = 'BUY'
        elif rsi > rsi_overbought:
            direction = 'SELL'
        else:
            return None
        
        # Risk management - use wider stops and larger TP for better R:R
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(close.iloc[-1]) * 0.001)
        stop_mult = float(p.get('stop_atr_mult', 1.0))  # slightly wider stop
        tp_mult = float(p.get('tp_atr_mult', 2.0))  # larger TP to improve R:R
        
        entry = float(close.iloc[-1])
        stop = entry - atr * stop_mult if direction == 'BUY' else entry + atr * stop_mult
        tp = entry + atr * tp_mult if direction == 'BUY' else entry - atr * tp_mult
        # Ensure TP is not too close to entry (at least 0.5 ATR)
        min_tp = 0.5 * atr
        if direction == 'BUY' and (tp - entry) < min_tp:
            tp = entry + min_tp
        if direction == 'SELL' and (entry - tp) < min_tp:
            tp = entry - min_tp
        
        # Improved confluence based on RSI extremeness and trend weakness
        rsi_extremeness = min(1.0, (abs(rsi - 50) / 25.0))  # 0 to 1 based on how far from 50
        trend_weakness = max(0.0, 1.0 - (trend_strength / max_trend_strength))
        conf = 0.4 + 0.4 * rsi_extremeness + 0.2 * trend_weakness

        # Reduce confidence during high ATR regimes
        avg_atr = float(m5['ATR'].rolling(window=20).mean().iloc[-1]) if 'ATR' in m5.columns else atr
        if atr > 1.8 * avg_atr:
            conf *= 0.75
        
        # Apply minimum confluence threshold
        if conf < float(p.get('min_confluence', 0.5)):
            return None

        return {
            'strategy_name': 'rsi_v2',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"rsi={rsi:.2f} oversold={rsi_oversold} overbought={rsi_overbought} trend={trend_strength:.4f}",
        }
    except Exception:
        return None
