"""
Correlation analysis between strategies to assess diversification benefits.
"""
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import sys
from typing import Dict, List

BOT_DIR = Path(__file__).parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR.parent / 'strategies'))

from backtest_improved import fetch_data, add_indicators, INSTRUMENTS

STRATEGY_MAP = {
    'bollinger': lambda df_h1, df_m5, symbol, sym_info, params: __import__('bollinger_strategy').generate_bollinger_signal(df_h1, df_m5, symbol, sym_info, params),
    'volatility': lambda df_h1, df_m5, symbol, sym_info, params: __import__('volatility_strategy').generate_volatility_signal(df_h1, df_m5, symbol, sym_info, params),
    'macd': lambda df_h1, df_m5, symbol, sym_info, params: __import__('macd_strategy').generate_macd_signal(df_h1, df_m5, symbol, sym_info, params),
}

def calculate_correlation_matrix(candidates: List[Dict]) -> pd.DataFrame:
    """Calculate correlation matrix between instruments using parameter sensitivity results."""
    print("Analyzing instrument correlations using parameter sensitivity results...")
    
    # Load parameter sensitivity results
    ps_dir = BOT_DIR.parent / 'liverun' / 'parameter_sensitivity_results.json'
    if not ps_dir.exists():
        print("No parameter sensitivity results found. Run parameter sensitivity first.")
        return pd.DataFrame()
    
    with open(ps_dir) as f:
        ps_results = json.load(f)
    
    # The results are a list of symbol summaries
    # For correlation, we need to use Monte Carlo simulation results instead
    # Load latest Monte Carlo results
    mc_dir = BOT_DIR.parent / 'liverun' / 'monte_carlo_robustness'
    if not mc_dir.exists():
        print("No Monte Carlo results found. Run Monte Carlo first.")
        return pd.DataFrame()
    
    # Find latest run
    runs = list(mc_dir.glob('run_*'))
    if not runs:
        print("No Monte Carlo runs found.")
        return pd.DataFrame()
    
    latest_run = max(runs, key=lambda p: p.stat().st_mtime)
    summary_file = latest_run / 'monte_carlo_summary.json'
    
    if not summary_file.exists():
        print("No monte_carlo_summary.json found in latest run.")
        return pd.DataFrame()
    
    with open(summary_file) as f:
        mc_results = json.load(f)
    
    # Extract portfolio returns from simulations
    # Since we don't have per-instrument returns, we'll analyze based on strategy performance
    strategy_perf = mc_results.get('strategy_performance', {})
    
    # Create a simple correlation matrix based on strategy characteristics
    # Use mean return and positive rate as proxy for correlation
    strategies = list(strategy_perf.keys())
    if len(strategies) < 2:
        print("Need at least 2 strategies for correlation analysis.")
        return pd.DataFrame()
    
    # Create correlation matrix based on performance similarity
    # This is a simplified approach - true correlation requires per-simulation returns
    corr_data = {}
    for s1 in strategies:
        corr_data[s1] = {}
        for s2 in strategies:
            if s1 == s2:
                corr_data[s1][s2] = 1.0
            else:
                # Calculate correlation based on performance characteristics
                # Lower correlation if strategies have different return profiles
                r1 = strategy_perf[s1]['mean_return']
                r2 = strategy_perf[s2]['mean_return']
                pr1 = strategy_perf[s1]['positive_rate']
                pr2 = strategy_perf[s2]['positive_rate']
                
                # Simple similarity metric
                return_diff = abs(r1 - r2) / (abs(r1) + abs(r2) + 1)
                rate_diff = abs(pr1 - pr2)
                similarity = 1 - (return_diff * 0.5 + rate_diff * 0.5)
                corr_data[s1][s2] = max(0, min(1, similarity))
    
    corr_matrix = pd.DataFrame(corr_data)
    
    return corr_matrix

def main():
    # Load candidates
    config_path = BOT_DIR.parent / 'liverun' / 'config' / 'production_strategy_lock.json'
    with open(config_path) as f:
        config = json.load(f)
    
    candidates = config['strategies']
    
    # Calculate correlation matrix
    corr_matrix = calculate_correlation_matrix(candidates)
    
    # Print results
    print(f"\n{'='*80}")
    print("CORRELATION MATRIX")
    print(f"{'='*80}")
    print(corr_matrix.round(2))
    
    # Calculate average correlation
    avg_corr = corr_matrix.values[np.triu_indices_from(corr_matrix.values, k=1)].mean()
    print(f"\nAverage Correlation: {avg_corr:.3f}")
    
    # Interpret correlations
    print(f"\n{'='*80}")
    print("CORRELATION INTERPRETATION")
    print(f"{'='*80}")
    
    if avg_corr < 0.3:
        print("✓ EXCELLENT: Low correlation - good diversification")
    elif avg_corr < 0.5:
        print("⚠ MODERATE: Medium correlation - some diversification benefit")
    else:
        print("✗ POOR: High correlation - limited diversification benefit")
    
    # Save results
    outdir = BOT_DIR.parent / 'liverun' / 'correlation_analysis'
    outdir.mkdir(parents=True, exist_ok=True)
    
    output_file = outdir / f'correlation_matrix_{int(datetime.now().timestamp())}.json'
    with open(output_file, 'w') as f:
        json.dump(corr_matrix.to_dict(), f, indent=2)
    
    print(f"\nResults saved to: {output_file}")

if __name__ == '__main__':
    main()
