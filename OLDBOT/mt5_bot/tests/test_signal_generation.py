"""Debug script to test signal generation for new strategies"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import numpy as np
from backtest_improved import fetch_data, add_indicators
import liquidity_sweep_strategy
import session_strategy

# Test with one symbol to debug
symbol = 'XAUUSD'
df = fetch_data(symbol, bars=2000)

if df is not None and len(df) >= 100:
    # Resample to timeframes
    df_h1 = df.resample('1h').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
    }).dropna()
    df_m5 = df.resample('5min').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
    }).dropna()
    
    # Add indicators
    df_h1 = add_indicators(df_h1)
    df_m5 = add_indicators(df_m5)
    
    sym_info = {'pip_size': 0.1, 'spread': 3.0, 'vol': 1.5}
    
    # Test liquidity sweep
    print("Testing liquidity_sweep strategy...")
    liquidity_signals = 0
    for i in range(100, len(df_m5) - 10, 10):
        df_h1_hist = df_h1.iloc[:i//12].copy()
        df_m5_hist = df_m5.iloc[:i].copy()
        
        signal = liquidity_sweep_strategy.generate_liquidity_sweep_signal(
            df_h1_hist, df_m5_hist, symbol, sym_info, 
            {'lookback_bars': 24, 'sweep_threshold': 0.3, 'rejection_threshold': 0.5}
        )
        if signal:
            liquidity_signals += 1
            print(f"  Liquidity signal at {i}: {signal['direction']} @ {signal['entry']:.2f}")
    
    print(f"Total liquidity signals: {liquidity_signals}")
    
    # Test session strategy
    print("\nTesting session strategy...")
    session_signals = 0
    for i in range(100, len(df_m5) - 10, 10):
        df_h1_hist = df_h1.iloc[:i//12].copy()
        df_m5_hist = df_m5.iloc[:i].copy()
        
        signal = session_strategy.generate_session_signal(
            df_h1_hist, df_m5_hist, symbol, sym_info,
            {'preferred_sessions': ['london', 'new_york', 'overlap']}
        )
        if signal:
            session_signals += 1
            print(f"  Session signal at {i}: {signal['direction']} @ {signal['entry']:.2f}")
    
    print(f"Total session signals: {session_signals}")
else:
    print("Failed to fetch data")
