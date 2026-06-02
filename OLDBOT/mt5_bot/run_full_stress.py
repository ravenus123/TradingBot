import importlib.util, json, sys
from pathlib import Path
P = Path(__file__).resolve()
BT_PATH = str(P.parent / 'backtest_improved.py')
spec = importlib.util.spec_from_file_location('bt', BT_PATH)
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

out = {'timestamp': None, 'results': {}}
for sym in list(bt.INSTRUMENTS.keys()):
    print('Running stress suite for', sym)
    df = bt.fetch_data(sym, bars=20000)
    if df is None:
        print('No data for', sym)
        out['results'][sym] = {'error': 'no data'}
        continue
    r = bt.run_stress_suite(sym, df)
    out['results'][sym] = r
    # write intermediate file
    p = Path(__file__).parent / 'liverun' / 'stress_tests' / f'full_stress_{sym}.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w') as fh:
        json.dump(r, fh, indent=2, default=str)

out['timestamp'] = __import__('datetime').datetime.now().isoformat()
with open(Path(__file__).parent / 'liverun' / 'stress_tests' / 'full_stress_summary.json', 'w') as fh:
    json.dump(out, fh, indent=2, default=str)

print('Done. Summary saved to liverun/stress_tests/full_stress_summary.json')
