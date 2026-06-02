"""Volatility expansion strategy.
Enters when volatility expands beyond a threshold, capturing momentum from volatility spikes.
Edge: Volatility expansion often precedes sustained moves as institutional capital enters.
Who loses: Traders who fade volatility expansion without understanding the underlying order flow.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_volatility_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    Volatility strategy: enters when realized volatility expands beyond threshold.
    Edge: Captures momentum from volatility expansion as institutional capital enters.
    Who loses: Traders who fade volatility expansion without understanding order flow.
    """
    try:
        p = params or {}
        
        # Use 5m data for volatility analysis
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        close = m5['Close'].astype(float)
        high = m5['High'].astype(float)
        low = m5['Low'].astype(float)
        
        if len(close) < 50:
            return None
        
        # Parameters
        volatility_window = int(p.get('volatility_window', 20))
        volatility_threshold = float(p.get('volatility_threshold', 1.5))
        
        # Calculate realized volatility (rolling std of returns)
        returns = close.pct_change()
        realized_vol = returns.rolling(window=volatility_window).std()
        
        # Calculate average volatility over longer period
        avg_vol = realized_vol.rolling(window=volatility_window * 2).mean()
        
        if len(realized_vol) < volatility_window * 2 + 5:
            return None
        
        # Current values
        current_vol = float(realized_vol.iloc[-1])
        avg_vol_current = float(avg_vol.iloc[-1])
        vol_ratio = current_vol / max(1e-8, avg_vol_current)
        
        # Generate signals based on volatility expansion
        # Volatility expansion with price direction
        price_change = float(close.iloc[-1] - close.iloc[-5])
        
        if vol_ratio > volatility_threshold:
            if price_change > 0:
                direction = 'BUY'
            else:
                direction = 'SELL'
        else:
            return None
        
        # Risk management
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(close.iloc[-1]) * 0.001)
        stop_mult = float(p.get('stop_atr_mult', 0.7))
        tp_mult = float(p.get('tp_atr_mult', 2.0))
        
        entry = float(close.iloc[-1])
        stop = entry - atr * stop_mult if direction == 'BUY' else entry + atr * stop_mult
        tp = entry + atr * tp_mult if direction == 'BUY' else entry - atr * tp_mult
        
        # Confluence based on volatility expansion strength and price momentum
        vol_strength = min(1.0, (vol_ratio - 1.0) / volatility_threshold)
        price_momentum = min(1.0, abs(price_change) / max(1e-8, atr))
        conf = 0.4 + 0.4 * vol_strength + 0.2 * price_momentum
        
        return {
            'strategy_name': 'volatility_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"vol_ratio={vol_ratio:.2f} threshold={volatility_threshold} atr={atr:.6f}",
        }
    except Exception:
        return None
