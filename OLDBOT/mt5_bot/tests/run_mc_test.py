from pathlib import Path
import monte_carlo_robustness as mcr

if __name__ == '__main__':
    candidates = mcr.load_candidates(limit=None)
    outdir = Path(__file__).parent / 'liverun' / 'monte_results'
    outdir.mkdir(parents=True, exist_ok=True)
    results = mcr.run_monte_carlo_test(candidates, outdir, bars=2000, num_simulations=5, period_days=10, risk_pct=1.0, seed=42)
    import json
    with open(outdir / 'summary.json', 'w') as f:
        json.dump(results, f, indent=2)
    print('Monte Carlo run complete. Results saved to', outdir)
