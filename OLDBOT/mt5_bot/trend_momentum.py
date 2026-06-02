"""Simple trend momentum strategy.
Uses H1 EMAs for trend; M5 breakout for entries.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np


def generate_trend_momentum_signal(df_h1: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    try:
        p = params or {}
        # H1 trend
        h1 = df_h1.copy()
        m5 = df_5m.copy()
        if len(h1) < 200 or len(m5) < 50:
            return None

        close_h1 = h1['Close'].astype(float)
        ema_fast = close_h1.ewm(span=int(p.get('ema_fast', 50)), adjust=False).mean()
        ema_slow = close_h1.ewm(span=int(p.get('ema_slow', 200)), adjust=False).mean()
        trend = 1 if ema_fast.iloc[-1] > ema_slow.iloc[-1] else -1

        # Use M5 ATR for sizing
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else 0.001
        recent_high = float(m5['High'].rolling(window=int(p.get('breakout_window', 24))).max().iloc[-2])
        recent_low = float(m5['Low'].rolling(window=int(p.get('breakout_window', 24))).min().iloc[-2])
        entry = float(m5['Close'].iloc[-1])

        # breakout threshold
        thr = float(p.get('breakout_thr_atr', 0.25)) * atr

        direction = None
        if trend == 1 and entry > recent_high + thr:
            direction = 'BUY'
        elif trend == -1 and entry < recent_low - thr:
            direction = 'SELL'
        else:
            return None

        stop_mult = float(p.get('stop_atr_mult', 2.0))
        tp_mult = float(p.get('tp_atr_mult', 4.0))
        stop = entry - atr * stop_mult if direction == 'BUY' else entry + atr * stop_mult
        tp = entry + atr * tp_mult if direction == 'BUY' else entry - atr * tp_mult

        # confluence: how strong the trend and breakout are
        trend_strength = abs(ema_fast.iloc[-1] - ema_slow.iloc[-1]) / max(1e-8, abs(ema_slow.iloc[-1]))
        breakout_strength = abs(entry - (recent_high if direction == 'BUY' else recent_low)) / max(1e-8, atr)
        conf = min(1.0, 0.4 + 0.4 * min(1.0, trend_strength * 50.0) + 0.2 * min(1.0, breakout_strength / 3.0))

        if conf < float(p.get('min_confluence', 0.35)):
            return None

        return {
            'strategy_name': 'trend_momentum_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"trend_strength={trend_strength:.4f} breakout={breakout_strength:.2f} atr={atr:.6f}",
        }
    except Exception:
        return None
