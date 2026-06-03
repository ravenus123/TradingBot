"""
Enhanced Mean Reversion Strategy

EDGE EXPLANATION (5 minutes):
==============================
WHY IT MAKES MONEY:
- Markets oscillate around mean - extreme moves tend to revert (statistical arbitrage)
- Bollinger bands provide statistical framework for identifying overextension
- Multi-timeframe confirmation improves signal quality
- RSI filter prevents mean reversion in strong trends

WHO'S ON THE LOSING SIDE:
- Breakout traders who chase moves beyond statistical extremes
- Trend followers entering at overextended levels
- Retail traders buying tops and selling bottoms without mean reversion understanding

WHAT THEY'RE DOING WRONG:
- Not understanding that markets spend most time ranging, not trending
- Chasing breakouts without statistical confirmation
- Not measuring overextension using statistical frameworks like Bollinger Bands
- Fighting the statistical edge of mean reversion

STRATEGY LOGIC:
===============
1. Use Bollinger Bands to identify statistical overextension
2. Multi-timeframe confirmation (H1 trend + M5 entry)
3. RSI filter to avoid mean reversion in strong trends
4. ADX filter to avoid mean reversion in trending markets
5. Enhanced confluence scoring for signal quality
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_enhanced_mean_reversion_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    Enhanced Mean Reversion: Improved version of bollinger strategy with additional filters.
    
    Edge: Statistical mean reversion with multi-timeframe confirmation.
    Who loses: Breakout traders and trend followers at overextended levels.
    """
    try:
        p = params or {}
        
        # Use 5m data for precise entries
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        h1 = add_indicators(df_1h, ema_period=20).fillna(0)
        
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
        
        # Enhanced filters
        
        # 1. RSI filter (avoid mean reversion in strong trends)
        rsi = float(m5['RSI'].iloc[-1]) if 'RSI' in m5.columns else 50.0
        rsi_overbought = float(p.get('rsi_overbought', 70.0))
        rsi_oversold = float(p.get('rsi_oversold', 30.0))
        
        # 2. ADX filter (avoid mean reversion in trending markets)
        adx = float(m5['ADX'].iloc[-1]) if 'ADX' in m5.columns else 0.0
        max_adx = float(p.get('max_adx', 30.0))
        
        # 3. H1 trend filter (align with higher timeframe)
        h1_close = h1['Close'].astype(float)
        h1_ema = h1_close.ewm(span=50, adjust=False).mean()
        h1_trend = 1 if h1_close.iloc[-1] > h1_ema.iloc[-1] else -1
        
        # Generate signals based on Bollinger Band touches with filters
        direction = None
        
        # SELL signal: price at upper band AND RSI overbought AND ADX not too high
        if percent_b >= 0.95 and rsi > rsi_overbought and adx < max_adx:
            # Only sell if H1 trend is not strongly bullish (avoid fighting strong uptrend)
            if h1_trend != 1:
                direction = 'SELL'
        
        # BUY signal: price at lower band AND RSI oversold AND ADX not too high
        elif percent_b <= 0.05 and rsi < rsi_oversold and adx < max_adx:
            # Only buy if H1 trend is not strongly bearish (avoid fighting strong downtrend)
            if h1_trend != -1:
                direction = 'BUY'
        
        else:
            return None
        
        # Risk management
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(current_close) * 0.001)
        stop_mult = float(p.get('stop_atr_mult', 0.8))
        tp_mult = float(p.get('tp_atr_mult', 2.0))
        
        entry = current_close
        stop = current_upper if direction == 'SELL' else current_lower
        stop = stop + atr * stop_mult if direction == 'SELL' else stop - atr * stop_mult
        tp = current_sma if direction == 'SELL' else current_sma
        tp = tp - atr * tp_mult if direction == 'SELL' else tp + atr * tp_mult
        
        # Enhanced confluence scoring
        band_extremeness = min(1.0, abs(percent_b - 0.5) / 0.45)
        rsi_extremeness = min(1.0, (abs(rsi - 50) / 30.0))
        adx_favorable = 1.0 - min(1.0, adx / max_adx)  # Lower ADX is better for mean reversion
        trend_alignment = 1.0 if h1_trend == 0 else 0.7  # Neutral trend is better
        
        conf = 0.3 + 0.3 * band_extremeness + 0.2 * rsi_extremeness + 0.1 * adx_favorable + 0.1 * trend_alignment
        
        return {
            'strategy_name': 'enhanced_mean_reversion_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"percent_b={percent_b:.2f} rsi={rsi:.2f} adx={adx:.2f} trend={h1_trend}",
        }
    except Exception:
        return None
