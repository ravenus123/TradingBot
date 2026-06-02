import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'liverun' / 'stress_tests'
OUT = ROOT / 'stress_analysis_summary.json'


def load_tests(path: Path):
    with open(path, 'r') as fh:
        return json.load(fh)


def summarize_symbol(payload):
    tests = payload.get('tests', {})
    rows = []
    for test_name, data in tests.items():
        metrics = data.get('metrics', {})
        rows.append({
            'test': test_name,
            'return_pct': float(metrics.get('return_pct', 0.0) or 0.0),
            'max_drawdown': float(metrics.get('max_drawdown', 0.0) or 0.0),
            'profit_factor': float(metrics.get('profit_factor', 0.0) or 0.0),
            'trades': int(metrics.get('total_trades', 0) or 0),
            'win_rate': float(metrics.get('win_rate', 0.0) or 0.0),
        })

    if not rows:
        return {'error': 'no tests'}

    baseline = next((r for r in rows if r['test'] == 'baseline'), rows[0])
    worst = min(rows, key=lambda r: r['return_pct'])
    most_dd = max(rows, key=lambda r: r['max_drawdown'])

    return {
        'symbol': payload.get('symbol'),
        'baseline': baseline,
        'worst_return_test': worst,
        'worst_drawdown_test': most_dd,
        'fails_on_spread_x2': rows and next((r for r in rows if r['test'] == 'spread_x2'), {'return_pct': None})['return_pct'] < 0,
        'fails_on_spread_x5': rows and next((r for r in rows if r['test'] == 'spread_x5'), {'return_pct': None})['return_pct'] < 0,
        'fails_on_worst_combined': rows and next((r for r in rows if r['test'] == 'worst_combined'), {'return_pct': None})['return_pct'] < 0,
        'tests': rows,
    }


def main():
    symbols = []
    summary = {}

    for path in sorted(ROOT.glob('*_stress.json')):
        if path.name == 'stress_analysis_summary.json':
            continue
        payload = load_tests(path)
        symbol = payload.get('symbol', path.stem.replace('_stress', ''))
        symbols.append(symbol)
        summary[symbol] = summarize_symbol(payload)

    rows = []
    for symbol, data in summary.items():
        worst = data.get('worst_return_test', {})
        baseline = data.get('baseline', {})
        rows.append({
            'symbol': symbol,
            'baseline_return_pct': baseline.get('return_pct', 0.0),
            'worst_return_pct': worst.get('return_pct', 0.0),
            'baseline_pf': baseline.get('profit_factor', 0.0),
            'worst_test': worst.get('test', ''),
            'spread_x2_return_pct': next((t['return_pct'] for t in data.get('tests', []) if t['test'] == 'spread_x2'), None),
            'spread_x5_return_pct': next((t['return_pct'] for t in data.get('tests', []) if t['test'] == 'spread_x5'), None),
            'worst_combined_return_pct': next((t['return_pct'] for t in data.get('tests', []) if t['test'] == 'worst_combined'), None),
            'fails_on_spread_x2': data.get('fails_on_spread_x2', False),
            'fails_on_spread_x5': data.get('fails_on_spread_x5', False),
            'fails_on_worst_combined': data.get('fails_on_worst_combined', False),
        })

    # Sort by worst-case fragility first
    rows.sort(key=lambda r: (r['worst_return_pct'] if r['worst_return_pct'] is not None else 0.0))

    print('Stress test summary across symbols:')
    for r in rows:
        print(
            f"{r['symbol']}: baseline={r['baseline_return_pct']:.2f}% | "
            f"worst={r['worst_return_pct']:.2f}% ({r['worst_test']}) | "
            f"x2={r['spread_x2_return_pct']:.2f}% | x5={r['spread_x5_return_pct']:.2f}% | "
            f"worst_combined={r['worst_combined_return_pct']:.2f}%"
        )

    report = {
        'symbols': symbols,
        'ranked_rows': rows,
        'by_symbol': summary,
    }
    with open(OUT, 'w') as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f'\nSaved analysis to {OUT}')


if __name__ == '__main__':
    main()
