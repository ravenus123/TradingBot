import importlib.util
import json
from pathlib import Path

P = Path(__file__).resolve()
BT_PATH = str(P.parent / 'backtest_improved.py')
spec = importlib.util.spec_from_file_location('bt', BT_PATH)
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY']


def main():
    results = {}
    for symbol in SYMBOLS:
        print(f"\n=== Robust optimize for {symbol} ===")
        df = bt.fetch_data(symbol)
        if df is None or len(df) < 300:
            results[symbol] = {'error': 'insufficient data'}
            continue

        best_params, best_result = bt.optimize(symbol, df, risk_pct=1.0)
        full_result = bt.run_backtest_no_lookahead(df.copy(), symbol, params=best_params, risk_pct=1.0)

        results[symbol] = {
            'best_params': list(best_params) if best_params else None,
            'opt_test_metrics': best_result.get('metrics', {}) if best_result else {},
            'full_metrics': full_result.get('metrics', {}),
        }

        print(json.dumps(results[symbol], indent=2))

    out_path = P.parent / 'liverun' / 'robust_optimize_results.json'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w') as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"\nSaved results to {out_path}")


if __name__ == '__main__':
    main()
