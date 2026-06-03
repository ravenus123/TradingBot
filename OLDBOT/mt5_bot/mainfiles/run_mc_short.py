from pathlib import Path
import json
from monte_carlo_robustness import load_candidates, run_monte_carlo_test

OUTDIR = Path(__file__).parent / 'liverun' / 'monte_short'
OUTDIR.mkdir(parents=True, exist_ok=True)

candidates = load_candidates()
res = run_monte_carlo_test(candidates, OUTDIR, bars=10000, num_simulations=20, period_days=30, risk_pct=1.0, seed=999)
with open(OUTDIR / 'summary.json', 'w') as f:
    json.dump(res, f, indent=2)
print('Done. Outputs ->', OUTDIR)
