"""Simple Breakout strategy module.
Detects low-volatility squeeze on 5m and captures directional breakout.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from mt5_bot.backtest_improved import add_indicators


def generate_breakout_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    try:
        p = params or {}
        m5 = add_indicators(df_5m, ema_period=int(p.get('ema_period', 20))).fillna(0)
        if len(m5) < 30:
            return None

        close = m5['Close'].astype(float)
        high = m5['High'].astype(float)
        low = m5['Low'].astype(float)

        # volatility: rolling std of close returns
        vol = close.pct_change().rolling(int(p.get('vol_window', 20))).std().iloc[-1]
        if vol is None or np.isnan(vol):
            return None

        # define squeeze threshold (low vol)
        squeeze_thresh = float(p.get('squeeze_thresh', 0.0006))
        if vol > squeeze_thresh:
            return None

        # breakout: compare latest close to range of last look bars
        look = int(p.get('look', 8))
        recent_high = high.iloc[-look:].max()
        recent_low = low.iloc[-look:].min()
        last = close.iloc[-1]
        prev_range = recent_high - recent_low
        if prev_range <= 0:
            return None

        # breakout if price closes outside range by small buffer
        buffer = prev_range * float(p.get('buffer_pct', 0.05))
        tp_mult = float(p.get('tp_mult', 1.5))
        if last > recent_high + buffer:
            direction = 'BUY'
            entry = float(last)
            stop = float(recent_low - buffer)
            tp = float(entry + prev_range * tp_mult)
        elif last < recent_low - buffer:
            direction = 'SELL'
            entry = float(last)
            stop = float(recent_high + buffer)
            tp = float(entry - prev_range * tp_mult)
        else:
            return None

        conf = 0.6 + min(0.35, prev_range / max(1e-6, entry))

        return {
            'strategy_name': 'breakout_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': stop,
            'tp': tp,
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"breakout look={look} prev_range={prev_range:.6f} vol={vol:.6f}",
        }
    except Exception:
        return None
