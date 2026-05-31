"""Volatility-based strategy module.
Generates signals based on volatility expansion/contraction and mean reversion in volatility.
Uncorrelated with price-based strategies - exploits volatility regime changes.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_volatility_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    Volatility strategy: enters when volatility expands after contraction period.
    Edge: Captures volatility mean reversion - volatility tends to return to mean after extreme expansion.
    Who loses: Traders who enter during low volatility periods expecting continuation, get stopped out on expansion.
    """
    try:
        p = params or {}
        
        # Use 5m data for volatility analysis
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        close = m5['Close'].astype(float)
        
        if len(close) < 50:
            return None
        
        # Calculate realized volatility
        returns = close.pct_change().dropna()
        vol_window = int(p.get('vol_window', 20))
        realized_vol = returns.rolling(vol_window).std() * np.sqrt(96)  # Annualized (96 15-min bars per day)
        
        if len(realized_vol) < vol_window + 10:
            return None
        
        current_vol = realized_vol.iloc[-1]
        avg_vol = realized_vol.rolling(vol_window * 2).mean().iloc[-1]
        vol_percentile = (realized_vol.rolling(vol_window * 3).rank(pct=True).iloc[-1] if len(realized_vol) > vol_window * 3 else 0.5)
        
        # Parameters
        vol_expansion_threshold = float(p.get('vol_expansion_threshold', 1.5))  # Vol must be 1.5x average
        vol_contraction_threshold = float(p.get('vol_contraction_threshold', 0.7))  # Vol must be 0.7x average
        
        # Strategy: Buy low vol, sell high vol (volatility mean reversion)
        # Or: Follow volatility expansion (breakout in volatility)
        mode = p.get('mode', 'expansion')  # 'expansion' or 'reversion'
        
        if mode == 'expansion':
            # Enter when volatility expands significantly
            if current_vol < avg_vol * vol_expansion_threshold:
                return None
            
            # Direction based on price trend during expansion
            price_change = (close.iloc[-1] - close.iloc[-vol_window]) / close.iloc[-vol_window]
            direction = 'BUY' if price_change > 0 else 'SELL'
            
        else:  # reversion
            # Enter when volatility is extreme (high or low percentile)
            if not (vol_percentile > 0.8 or vol_percentile < 0.2):
                return None
            
            # Buy low vol, sell high vol
            direction = 'BUY' if vol_percentile < 0.2 else 'SELL'
        
        # Risk management
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(close.iloc[-1]) * 0.001)
        stop_mult = float(p.get('stop_atr_mult', 1.5))
        tp_mult = float(p.get('tp_atr_mult', 2.0))
        
        entry = float(close.iloc[-1])
        stop = entry - atr * stop_mult if direction == 'BUY' else entry + atr * stop_mult
        tp = entry + atr * tp_mult if direction == 'BUY' else entry - atr * tp_mult
        
        # Confluence based on volatility extreme
        vol_extreme = abs(vol_percentile - 0.5) * 2  # 0 to 1
        conf = min(0.9, 0.5 + vol_extreme * 0.4)
        
        return {
            'strategy_name': 'volatility_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"volatility mode={mode} vol={current_vol:.6f} avg={avg_vol:.6f} pct={vol_percentile:.2f}",
        }
    except Exception:
        return None
