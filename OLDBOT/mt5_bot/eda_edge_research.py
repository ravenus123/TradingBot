"""
Exploratory Data Analysis for Edge Research
Analyzes price movements after specific conditions to find skewed probabilities.
"""
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from datetime import datetime, timedelta
from pathlib import Path

def fetch_data(symbol, timeframe, bars=10000):
    """Fetch historical data from MT5"""
    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}")
        return None
    
    mt5_timeframe = mt5.TIMEFRAME_M15 if timeframe == 'M15' else mt5.TIMEFRAME_H1
    rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, bars)
    
    if rates is None or len(rates) == 0:
        print(f"No data for {symbol}")
        mt5.shutdown()
        return None
    
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    mt5.shutdown()
    
    return df

def analyze_sigma_drift(df, sigma_threshold=3.0, lookforward_periods=[1, 4, 12, 24]):
    """
    Analyze price drift after 3-sigma drops.
    This looks for mean reversion after extreme moves.
    """
    df = df.copy()
    
    # Calculate returns
    df['returns'] = df['close'].pct_change()
    
    # Calculate rolling mean and std
    window = 100
    df['rolling_mean'] = df['close'].rolling(window).mean()
    df['rolling_std'] = df['close'].rolling(window).std()
    
    # Calculate z-score
    df['z_score'] = (df['close'] - df['rolling_mean']) / df['rolling_std']
    
    # Find 3-sigma drops
    sigma_drops = df[df['z_score'] < -sigma_threshold].copy()
    
    if len(sigma_drops) == 0:
        print(f"No {sigma_threshold}-sigma drops found")
        return None
    
    results = {}
    
    for period in lookforward_periods:
        future_returns = []
        
        for idx, row in sigma_drops.iterrows():
            current_idx = df.index.get_loc(idx)
            if current_idx + period < len(df):
                future_close = df['close'].iloc[current_idx + period]
                current_close = row['close']
                future_return = (future_close - current_close) / current_close
                future_returns.append(future_return)
        
        if future_returns:
            results[f'{period}_period'] = {
                'mean': np.mean(future_returns),
                'std': np.std(future_returns),
                'count': len(future_returns),
                'positive_pct': sum(1 for r in future_returns if r > 0) / len(future_returns) * 100
            }
    
    return results

def analyze_session_drift(df, session_hours=None):
    """
    Analyze price drift during specific trading sessions.
    London: 08:00-16:00 UTC
    New York: 13:00-21:00 UTC
    Overlap: 13:00-16:00 UTC
    """
    if session_hours is None:
        session_hours = {
            'london': (8, 16),
            'new_york': (13, 21),
            'overlap': (13, 16)
        }
    
    df = df.copy()
    df['hour'] = df['time'].dt.hour
    df['returns'] = df['close'].pct_change()
    
    results = {}
    
    for session_name, (start_hour, end_hour) in session_hours.items():
        session_data = df[(df['hour'] >= start_hour) & (df['hour'] < end_hour)]
        
        if len(session_data) > 0:
            results[session_name] = {
                'mean_return': session_data['returns'].mean(),
                'std_return': session_data['returns'].std(),
                'count': len(session_data),
                'positive_pct': (session_data['returns'] > 0).sum() / len(session_data) * 100
            }
    
    return results

def analyze_candle_patterns(df):
    """
    Analyze price drift after specific candle patterns.
    Large candles, dojis, hammers, etc.
    """
    df = df.copy()
    
    # Calculate candle body and range
    df['body'] = abs(df['close'] - df['open'])
    df['range'] = df['high'] - df['low']
    df['body_to_range'] = df['body'] / df['range']
    
    # Large candles (body > 2x average body)
    avg_body = df['body'].rolling(100).mean()
    large_bullish = df[(df['body'] > 2 * avg_body) & (df['close'] > df['open'])]
    large_bearish = df[(df['body'] > 2 * avg_body) & (df['close'] < df['open'])]
    
    results = {}
    
    # Analyze drift after large bullish candles
    for period in [1, 4, 12]:
        future_returns = []
        for idx, row in large_bullish.iterrows():
            current_idx = df.index.get_loc(idx)
            if current_idx + period < len(df):
                future_return = (df['close'].iloc[current_idx + period] - row['close']) / row['close']
                future_returns.append(future_return)
        
        if future_returns:
            results[f'large_bullish_{period}p'] = {
                'mean': np.mean(future_returns),
                'positive_pct': sum(1 for r in future_returns if r > 0) / len(future_returns) * 100,
                'count': len(future_returns)
            }
    
    # Analyze drift after large bearish candles
    for period in [1, 4, 12]:
        future_returns = []
        for idx, row in large_bearish.iterrows():
            current_idx = df.index.get_loc(idx)
            if current_idx + period < len(df):
                future_return = (df['close'].iloc[current_idx + period] - row['close']) / row['close']
                future_returns.append(future_return)
        
        if future_returns:
            results[f'large_bearish_{period}p'] = {
                'mean': np.mean(future_returns),
                'positive_pct': sum(1 for r in future_returns if r > 0) / len(future_returns) * 100,
                'count': len(future_returns)
            }
    
    return results

def main():
    """Run EDA on all instruments"""
    symbols = ['XAUUSD', 'BTCUSD', 'SP500']
    
    print("=" * 80)
    print("EXPLORATORY DATA ANALYSIS FOR EDGE RESEARCH")
    print("=" * 80)
    
    for symbol in symbols:
        print(f"\n{'=' * 80}")
        print(f"ANALYZING {symbol}")
        print(f"{'=' * 80}")
        
        # Fetch M15 data
        df = fetch_data(symbol, 'M15', bars=10000)
        
        if df is None:
            continue
        
        # 1. Sigma drift analysis
        print("\n--- SIGMA DRIFT ANALYSIS (3-sigma drops) ---")
        sigma_results = analyze_sigma_drift(df)
        if sigma_results:
            for period, stats in sigma_results.items():
                print(f"{period}: Mean={stats['mean']:.4f}, Positive%={stats['positive_pct']:.1f}%, Count={stats['count']}")
        
        # 2. Session drift analysis
        print("\n--- SESSION DRIFT ANALYSIS ---")
        session_results = analyze_session_drift(df)
        for session, stats in session_results.items():
            print(f"{session}: Mean={stats['mean_return']:.6f}, Positive%={stats['positive_pct']:.1f}%, Count={stats['count']}")
        
        # 3. Candle pattern analysis
        print("\n--- CANDLE PATTERN ANALYSIS ---")
        pattern_results = analyze_candle_patterns(df)
        for pattern, stats in pattern_results.items():
            print(f"{pattern}: Mean={stats['mean']:.4f}, Positive%={stats['positive_pct']:.1f}%, Count={stats['count']}")

if __name__ == "__main__":
    main()
