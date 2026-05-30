"""Simple Trend / Momentum strategy module.
Generates signals when HTF (1H) trend aligns with LTF (5m) momentum.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from OLDBOT.mt5_bot.backtest_improved import add_indicators


def generate_trend_momentum_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    try:
        # HTF trend via EMA slope on 1h
        p = params or {}
        h1_period = int(p.get('h1_ema_period', 50))
        m5_ema = int(p.get('m5_ema_period', 20))
        h1 = add_indicators(df_1h, ema_period=h1_period).fillna(0)
        m5 = add_indicators(df_5m, ema_period=m5_ema).fillna(0)

        ema_h1 = h1['EMA'].astype(float).values
        ema_h1_slope = ema_h1[-1] - ema_h1[-5] if len(ema_h1) > 5 else 0.0

        ema_fast = m5['EMA_Fast'].astype(float).values
        ema_slow = m5['EMA'].astype(float).values

        # trend direction: 1 = bullish, -1 = bearish, 0 = neutral
        direction = 1 if ema_h1_slope > 0 else -1 if ema_h1_slope < 0 else 0
        if direction == 0:
            return None

        # momentum confirmation on 5m: fast EMA above slow EMA for buys
        if direction == 1 and ema_fast[-1] <= ema_slow[-1]:
            return None
        if direction == -1 and ema_fast[-1] >= ema_slow[-1]:
            return None

        close = float(m5['Close'].iloc[-1])
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else 0.0
        if atr <= 0:
            atr = max(0.0005, abs(close) * float(p.get('atr_scale_fallback', 0.001)))

        # entry at next bar open (approx current close), stop and tp scaled by params
        stop_mult = float(p.get('stop_atr_mult', 1.5))
        tp_mult = float(p.get('tp_atr_mult', 3.0))
        side = 'BUY' if direction == 1 else 'SELL'
        entry = close
        stop = entry - atr * stop_mult if direction == 1 else entry + atr * stop_mult
        tp = entry + atr * tp_mult if direction == 1 else entry - atr * tp_mult

        conf = 0.6 + min(0.4, abs(ema_h1_slope) / max(1e-6, abs(ema_h1[-1])))

        return {
            'strategy_name': 'trend_momentum_v1',
            'symbol': symbol,
            'direction': side,
            'entry': float(entry),
            'stop': float(stop),
            'tp': float(tp),
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"trend_momentum EMA1H_slope={ema_h1_slope:.6f} atr={atr:.6f}",
        }
    except Exception:
        return None
