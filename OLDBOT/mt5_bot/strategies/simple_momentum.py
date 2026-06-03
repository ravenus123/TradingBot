"""
Simple Momentum Strategy

EDGE EXPLANATION (5 minutes):
==============================
WHY IT MAKES MONEY:
- Price momentum persists in the short term (behavioral bias: herding, underreaction)
- Markets don't instantly price in new information - creates sustained moves
- Risk premium: You get paid for holding positions through volatility
- Complements mean reversion: different market regimes

WHO'S ON THE LOSING SIDE:
- Traders who fight strong momentum (fade rallies/drops)
- Traders with tight stops in trending markets get shaken out
- Mean reversion traders in strongly trending markets
- Retail traders trying to pick tops/bottoms

WHAT THEY'RE DOING WRONG:
- Underestimating how far trends can extend
- Taking profits too early in momentum moves
- Not understanding that markets trend more than they range
- Fighting the trend instead of following it

STRATEGY LOGIC:
===============
1. Simple price momentum (price X periods ago vs current)
2. Volume confirmation
3. ATR-based risk management
4. Larger targets (trends extend further than expected)
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_simple_momentum_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    Simple Momentum: Price momentum with volume confirmation.
    
    Edge: Short-term price persistence and risk premium.
    Who loses: Traders who fight momentum and pick tops/bottoms.
    """
    try:
        p = params or {}
        
        # Use 5m data
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        close = m5['Close'].astype(float)
        high = m5['High'].astype(float)
        low = m5['Low'].astype(float)
        
        if len(close) < 50:
            return None
        
        # Parameters
        lookback = int(p.get('lookback', 20))  # Lookback period for momentum
        momentum_threshold = float(p.get('momentum_threshold', 0.5))  # In ATR units
        volume_confirmation = p.get('volume_confirmation', True)
        
        # Calculate momentum (price change over lookback)
        price_change = close.iloc[-1] - close.iloc[-lookback]
        
        # ATR for normalization
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(close.iloc[-1]) * 0.001)
        
        # Momentum in ATR units
        momentum_atr = abs(price_change) / max(1e-8, atr)
        
        # Check if momentum meets threshold
        if momentum_atr < momentum_threshold:
            return None
        
        # Determine direction
        direction = 'BUY' if price_change > 0 else 'SELL'
        
        # Volume confirmation
        if volume_confirmation and 'Volume' in m5.columns:
            avg_volume = m5['Volume'].rolling(window=lookback).mean().iloc[-lookback]
            current_volume = m5['Volume'].iloc[-1]
            if current_volume < avg_volume * 1.1:  # Need volume confirmation
                return None
        
        # Risk management
        stop_mult = float(p.get('stop_atr_mult', 1.5))
        tp_mult = float(p.get('tp_atr_mult', 3.0))
        
        entry = float(close.iloc[-1])
        stop = entry - atr * stop_mult if direction == 'BUY' else entry + atr * stop_mult
        tp = entry + atr * tp_mult if direction == 'BUY' else entry - atr * tp_mult
        
        # Confluence based on momentum strength
        momentum_strength = min(1.0, momentum_atr / 2.0)
        conf = 0.5 + 0.3 * momentum_strength + 0.2  # Base 0.5
        
        return {
            'strategy_name': 'simple_momentum_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"momentum_atr={momentum_atr:.2f} price_change={price_change:.2f} atr={atr:.6f}",
        }
    except Exception:
        return None
