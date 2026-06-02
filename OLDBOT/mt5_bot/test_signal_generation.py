"""Test signal generation for EURUSD/GBPUSD to diagnose 0% returns."""
import json
from pathlib import Path
import pandas as pd

from backtest_improved import fetch_data, add_indicators
from mean_reversion import generate_mean_reversion_signal
from rsi_strategy import generate_rsi_signal
from stochastic_strategy import generate_stochastic_signal

BOT_DIR = Path(__file__).parent
CONFIG_FILE = BOT_DIR / 'liverun' / 'config' / 'production_strategy_lock.json'


def load_candidates() -> list:
    """Load candidate strategies from production_strategy_lock.json"""
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    return config['strategies']


def test_symbol_signals(symbol: str, bars: int = 5000):
    """Test signal generation for a specific symbol."""
    print(f"\n=== Testing signal generation for {symbol} ===")
    
    # Load config
    candidates = load_candidates()
    symbol_candidates = [c for c in candidates if c['symbol'] == symbol]
    
    print(f"Found {len(symbol_candidates)} strategy configurations for {symbol}")
    
    # Fetch data
    df_m15 = fetch_data(symbol, bars=bars)
    if df_m15 is None:
        print(f"Failed to fetch data for {symbol}")
        return
    
    # Resample to H1 and M5
    df_1h = df_m15.resample('1h').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
    }).dropna()
    df_5m = df_m15.resample('5min').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
    }).dropna()
    
    df_1h = df_1h.reset_index()
    df_5m = df_5m.reset_index()
    
    # Add indicators
    df_1h = add_indicators(df_1h, ema_period=20).fillna(0)
    df_5m = add_indicators(df_5m, ema_period=20).fillna(0)
    
    print(f"Data loaded: H1={len(df_1h)} bars, M5={len(df_5m)} bars")
    
    # Test each strategy
    STRATEGY_MAP = {
        'mean_reversion': generate_mean_reversion_signal,
        'rsi': generate_rsi_signal,
        'stochastic': generate_stochastic_signal,
    }
    
    for candidate in symbol_candidates:
        strategy = candidate['strategy']
        params = candidate['params']
        
        print(f"\n--- Testing {strategy} ---")
        print(f"Parameters: {params}")
        
        gen = STRATEGY_MAP.get(strategy)
        if not gen:
            print(f"Strategy {strategy} not found in map")
            continue
        
        # Try to generate signals at different points in the data
        signals_found = 0
        test_points = [100, 200, 300, 500, 1000, 2000]
        
        for point in test_points:
            if point >= len(df_5m):
                continue
            
            df_h1_hist = df_1h.iloc[:point//12]
            df_m5_hist = df_5m.iloc[:point]
            
            if len(df_h1_hist) < 50 or len(df_m5_hist) < 50:
                continue
            
            signal = gen(df_h1_hist, df_m5_hist, symbol, {}, params)
            if signal:
                signals_found += 1
                print(f"  Signal at bar {point}: {signal['direction']} @ {signal['entry']:.5f}, "
                      f"conf={signal['confluence_score']:.2f}")
        
        print(f"Total signals found: {signals_found}/{len(test_points)} test points")
        
        if signals_found == 0:
            print(f"⚠ NO SIGNALS GENERATED - parameters may be too restrictive")
            print(f"  Suggest relaxing thresholds for {symbol}")


if __name__ == '__main__':
    # Test EURUSD and GBPUSD specifically
    test_symbol_signals('EURUSD', bars=5000)
    test_symbol_signals('GBPUSD', bars=5000)
    
    # Also test NAS100 for comparison
    test_symbol_signals('NAS100', bars=5000)
