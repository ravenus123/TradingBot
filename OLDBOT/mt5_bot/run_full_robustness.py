from pathlib import Path
import json

from monte_carlo_robustness import load_candidates, run_monte_carlo_test
import walk_forward_test
import parameter_sensitivity

OUTDIR = Path(__file__).parent / 'liverun' / 'full_robustness'
OUTDIR.mkdir(parents=True, exist_ok=True)

if __name__ == '__main__':
    candidates = load_candidates()

    # Monte Carlo
    mc_out = OUTDIR / 'monte'
    mc_out.mkdir(parents=True, exist_ok=True)
    mc_res = run_monte_carlo_test(candidates, mc_out, bars=10000, num_simulations=30, period_days=30, risk_pct=1.0, seed=12345)
    with open(mc_out / 'summary.json', 'w') as f:
        json.dump(mc_res, f, indent=2)
    print('Monte Carlo finished ->', mc_out)

    # Walk-forward
    wf_out = OUTDIR / 'walkforward'
    wf_out.mkdir(parents=True, exist_ok=True)
    wf_res = walk_forward_test.run_walk_forward_test(num_periods=10, bars_per_period=3000)
    with open(wf_out / 'walkforward_results.json', 'w') as f:
        json.dump(wf_res, f, indent=2)
    print('Walk-forward finished ->', wf_out)

    # Parameter sensitivity
    ps_out = OUTDIR / 'param_sensitivity'
    ps_out.mkdir(parents=True, exist_ok=True)
    parameter_sensitivity.run_parameter_sensitivity(num_variations=50)
    print('Parameter sensitivity finished ->', ps_out)

    print('\nFull robustness suite complete. Results under:', OUTDIR)
