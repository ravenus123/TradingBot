"""Simple Mean Reversion strategy module.
Uses z-score over short window to enter against short-term overextension.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from backtest_improved import add_indicators


def generate_mean_reversion_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    try:
        p = params or {}
        ema_period = int(p.get('ema_period', 20))
        m5 = add_indicators(df_5m, ema_period=ema_period).fillna(0)
        close = m5['Close'].astype(float)
        if len(close) < 30:
            return None

        window = int(p.get('window', 14))  # Changed from 20 to 14 for faster reaction
        mean = close.rolling(window).mean().iloc[-1]
        std = close.rolling(window).std().iloc[-1]
        if std == 0 or np.isnan(std):
            return None
        z = (close.iloc[-1] - mean) / std

        # RSI filter to avoid mean reversion in strong trends
        rsi = float(m5['RSI'].iloc[-1]) if 'RSI' in m5.columns else 50.0
        # Only mean revert when RSI is extreme (overbought/oversold)
        if not (rsi < 30 or rsi > 70):
            return None

        # Regime filter: avoid mean reversion in strong trending markets (ADX high)
        adx = float(m5['ADX'].iloc[-1]) if 'ADX' in m5.columns else 0.0
        max_adx = float(p.get('max_adx', 25.0))
        if adx > max_adx:
            return None

        z_th = float(p.get('z_threshold', 1.5))  # Lower threshold from 2.0 to 1.5 for more signals
        # thresholds: enter when |z| >= z_th
        if abs(z) < z_th:
            return None

        direction = 'BUY' if z < 0 else 'SELL'
        entry = float(close.iloc[-1])
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(entry) * float(p.get('atr_fallback', 0.001)))
        # Use slightly wider stops and keep target anchored to mean but ensure TP distance >= 0.5 ATR
        stop_mult = float(p.get('stop_atr_mult', 1.0))
        stop = entry - atr * stop_mult if direction == 'BUY' else entry + atr * stop_mult
        tp = float(mean)  # target mean
        # Ensure TP is at least 0.5 ATR away for meaningful R:R
        min_tp_dist = 0.5 * atr
        if direction == 'BUY' and abs(tp - entry) < min_tp_dist:
            tp = entry + min_tp_dist
        if direction == 'SELL' and abs(tp - entry) < min_tp_dist:
            tp = entry - min_tp_dist

        # Improved confluence: stronger when |z| larger AND RSI is extreme
        rsi_extremeness = min(1.0, (abs(rsi - 50) / 30.0))
        z_strength = min(1.0, abs(z) / 3.0)
        conf = 0.4 + 0.3 * rsi_extremeness + 0.3 * z_strength
        # Penalize if ATR is unusually large (too noisy)
        avg_atr = float(m5['ATR'].rolling(window=20).mean().iloc[-1]) if 'ATR' in m5.columns else atr
        if atr > 2.0 * avg_atr:
            conf *= 0.7

        # Require minimum confluence to avoid low-quality signals
        if conf < float(p.get('min_confluence', 0.5)):
            return None

        return {
            'strategy_name': 'mean_reversion_v1',
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'stop': float(stop),
            'tp': tp,
            'confluence_score': float(conf),
            'timestamp': datetime.utcnow().isoformat(),
            'params': f"mean_reversion z={z:.2f} mean={mean:.5f} std={std:.5f} rsi={rsi:.2f}",
        }
    except Exception:
        return None
