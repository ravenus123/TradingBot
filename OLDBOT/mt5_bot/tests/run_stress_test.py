import importlib.util, json, sys
from pathlib import Path

P = Path(__file__).resolve()
BT_PATH = str(P.parent / 'backtest_improved.py')
spec = importlib.util.spec_from_file_location('bt', BT_PATH)
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

def main():
    symbols = list(bt.INSTRUMENTS.keys())
    results = {}
    for sym in symbols:
        print(f"\n=== Stress-testing {sym} ===")
        df = bt.fetch_data(sym, bars=20000)
        if df is None:
            results[sym] = {'error': 'no data'}
            print(f'No data for {sym}')
            continue
        results[sym] = bt.run_stress_suite(sym, df)
        print(json.dumps(results[sym], indent=2))

    summary = {
        'timestamp': bt.datetime.now().isoformat(),
        'symbols': symbols,
        'results': results,
    }
    out_path = Path(__file__).parent / 'liverun' / 'stress_tests' / 'all_symbols_stress_summary.json'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w') as fh:
        json.dump(summary, fh, indent=2, default=str)
    print(f"\nSaved summary to {out_path}")

if __name__ == '__main__':
    main()
