"""Simple Mean Reversion strategy module.
Uses z-score over short window to enter against short-term overextension.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from mt5_bot.backtest_improved import add_indicators


def generate_mean_reversion_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    try:
        p = params or {}
        ema_period = int(p.get('ema_period', 20))
        m5 = add_indicators(df_5m, ema_period=ema_period).fillna(0)
        close = m5['Close'].astype(float)
        if len(close) < 30:
            return None

        window = int(p.get('window', 20))
        mean = close.rolling(window).mean().iloc[-1]
        std = close.rolling(window).std().iloc[-1]
        if std == 0 or np.isnan(std):
            return None
        z = (close.iloc[-1] - mean) / std

        z_th = float(p.get('z_threshold', 2.0))
        # thresholds: enter when |z| >= z_th
        if abs(z) < z_th:
            return None

        direction = 'BUY' if z < 0 else 'SELL'
        entry = float(close.iloc[-1])
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(entry) * float(p.get('atr_fallback', 0.001)))
        stop_mult = float(p.get('stop_atr_mult', 1.0))
        stop = entry - atr * stop_mult if direction == 'BUY' else entry + atr * stop_mult
        tp = float(mean)  # target mean

        # Estimate confluence: stronger when |z| larger
        conf = min(0.95, 0.4 + min(0.6, abs(z) / max(1e-6, float(p.get('z_scale', 3.0)))))

        return {
            'strategy_name': 'mean_reversion_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': tp,
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"mean_reversion z={z:.2f} mean={mean:.5f} std={std:.5f}",
        }
    except Exception:
        return None
