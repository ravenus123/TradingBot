"""
Liquidity Sweep / Stop Hunt Strategy

EDGE EXPLANATION (5 minutes):
==============================
WHY IT MAKES MONEY:
- Large institutional orders need significant liquidity to fill without slippage
- Retail traders place stops at obvious levels (previous highs/lows, round numbers)
- Institutions deliberately push price into these "liquidity pools" to fill their orders
- After sweeping liquidity, price often reverses sharply in the institutional direction

WHO'S ON THE LOSING SIDE:
- Retail traders with tight stops placed at obvious technical levels
- Breakout traders who get faked out by false breakouts
- Traders following textbook technical analysis without understanding order flow

WHAT THEY'RE DOING WRONG:
- Placing stops at obvious levels (previous highs/lows, round numbers)
- Not understanding that markets are driven by liquidity needs, not technical patterns
- Fading strong momentum moves that are actually liquidity sweeps

STRATEGY LOGIC:
===============
1. Identify potential liquidity levels (recent highs/lows, round numbers)
2. Detect price action that sweeps these levels (quick spike then rejection)
3. Enter in the direction of the rejection after liquidity is swept
4. Target: Return to the origin before the sweep
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np
import math

from backtest_improved import add_indicators


def generate_liquidity_sweep_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str, sym_info: dict, params: dict | None = None) -> Optional[dict]:
    """
    Liquidity Sweep Strategy: Detects and trades stop hunts/liquidity sweeps.
    
    Edge: Institutions sweep liquidity at key levels to fill large orders.
    Who loses: Retail traders with stops at obvious technical levels.
    """
    try:
        p = params or {}
        
        # Use 5m data for precise sweep detection
        m5 = add_indicators(df_5m, ema_period=20).fillna(0)
        close = m5['Close'].astype(float)
        high = m5['High'].astype(float)
        low = m5['Low'].astype(float)
        
        if len(close) < 50:
            return None
        
        # Parameters
        lookback_bars = int(p.get('lookback_bars', 24))  # Lookback for liquidity levels
        sweep_threshold = float(p.get('sweep_threshold', 0.3))  # How far past level to qualify as sweep (in ATR)
        rejection_threshold = float(p.get('rejection_threshold', 0.5))  # How strong the rejection must be (in ATR)
        volume_confirmation = p.get('volume_confirmation', True)
        
        # Calculate ATR for distance measurements
        atr = float(m5['ATR'].iloc[-1]) if 'ATR' in m5.columns else max(0.0005, abs(close.iloc[-1]) * 0.001)
        
        # Find recent liquidity levels (highs and lows from lookback period)
        recent_high = high.rolling(window=lookback_bars).max().iloc[-2]
        recent_low = low.rolling(window=lookback_bars).min().iloc[-2]
        
        # Also consider round numbers for forex/indices
        current_price = float(close.iloc[-1])
        round_number_levels = []
        
        # For indices/crypto, round to 100 or 50
        if symbol in ['BTCUSD', 'SP500', 'NAS100']:
            round_number_levels.append(math.floor(current_price / 100) * 100)
            round_number_levels.append(math.floor(current_price / 100) * 100 + 100)
        # For forex/gold, round to 0.01 or 0.1
        elif symbol in ['EURUSD', 'GBPUSD', 'XAUUSD']:
            if symbol == 'XAUUSD':
                round_number_levels.append(math.floor(current_price / 50) * 50)
                round_number_levels.append(math.floor(current_price / 50) * 50 + 50)
            else:
                round_number_levels.append(math.floor(current_price / 0.01) * 0.01)
                round_number_levels.append(math.floor(current_price / 0.01) * 0.01 + 0.01)
        
        # Combine all potential liquidity levels
        liquidity_levels = [recent_high, recent_low] + round_number_levels
        
        # Check for sweep patterns
        for level in liquidity_levels:
            # Skip if level is too close to current price
            if abs(current_price - level) < atr * 0.2:
                continue
            
            # Check for bullish sweep (price swept below a low, then rejected upward)
            # Fixed: check if recent bar swept below level, not current bar
            if low.iloc[-2] < level and current_price > level:
                # Price swept below the level (liquidity sweep)
                sweep_distance = level - low.iloc[-2]
                if sweep_distance < atr * sweep_threshold:
                    continue  # Sweep not significant enough
                
                # Check for rejection (price moved back above level)
                rejection_distance = current_price - level
                if rejection_distance < atr * rejection_threshold:
                    continue  # Rejection not strong enough
                
                # Volume confirmation
                if volume_confirmation and 'Volume' in m5.columns:
                    avg_volume = m5['Volume'].rolling(window=lookback_bars).mean().iloc[-2]
                    if m5['Volume'].iloc[-1] < avg_volume * 1.2:
                        continue  # Volume too low
                
                # Bullish sweep detected
                direction = 'BUY'
                entry = current_price
                stop = low.iloc[-1] - atr * 0.5  # Below the sweep low
                tp = level + atr * 2.0  # Target return to level + extension
                
                # Confluence score based on sweep strength and rejection strength
                sweep_strength = min(1.0, sweep_distance / (atr * 2.0))
                rejection_strength = min(1.0, rejection_distance / (atr * 1.5))
                conf = 0.5 + 0.3 * sweep_strength + 0.2 * rejection_strength
                
                return {
                    'strategy_name': 'liquidity_sweep_v1',
                    'symbol': symbol,
                    'direction': direction,
                    'entry': entry,
                    'stop': float(stop),
                    'tp': float(tp),
                    'confluence_score': float(conf),
                    'timestamp': datetime.utcnow().isoformat(),
                    'params': f"sweep_dist={sweep_distance:.2f} reject_dist={rejection_distance:.2f} level={level:.2f}",
                }
            
            # Check for bearish sweep (price swept above a high, then rejected downward)
            # Fixed: check if recent bar swept above level, not current bar
            elif high.iloc[-2] > level and current_price < level:
                # Price swept above the level (liquidity sweep)
                sweep_distance = high.iloc[-2] - level
                if sweep_distance < atr * sweep_threshold:
                    continue  # Sweep not significant enough
                
                # Check for rejection (price moved back below level)
                rejection_distance = level - current_price
                if rejection_distance < atr * rejection_threshold:
                    continue  # Rejection not strong enough
                
                # Volume confirmation
                if volume_confirmation and 'Volume' in m5.columns:
                    avg_volume = m5['Volume'].rolling(window=lookback_bars).mean().iloc[-2]
                    if m5['Volume'].iloc[-1] < avg_volume * 1.2:
                        continue  # Volume too low
                
                # Bearish sweep detected
                direction = 'SELL'
                entry = current_price
                stop = high.iloc[-1] + atr * 0.5  # Above the sweep high
                tp = level - atr * 2.0  # Target return to level - extension
                
                # Confluence score based on sweep strength and rejection strength
                sweep_strength = min(1.0, sweep_distance / (atr * 2.0))
                rejection_strength = min(1.0, rejection_distance / (atr * 1.5))
                conf = 0.5 + 0.3 * sweep_strength + 0.2 * rejection_strength
                
                return {
                    'strategy_name': 'liquidity_sweep_v1',
                    'symbol': symbol,
                    'direction': direction,
                    'entry': entry,
                    'stop': float(stop),
                    'tp': float(tp),
                    'confluence_score': float(conf),
                    'timestamp': datetime.utcnow().isoformat(),
                    'params': f"sweep_dist={sweep_distance:.2f} reject_dist={rejection_distance:.2f} level={level:.2f}",
                }
        
        return None
    except Exception:
        return None
