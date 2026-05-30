from __future__ import annotations
# --- path bootstrap (allow running as a script: add BOT/ to sys.path) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

if __package__ in (None, ''):
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from mt5_bot import backtest_improved as bt
from mt5_bot import smart_money_strategy as sm


DEFAULT_INSTRUMENTS = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD', 'NAS100', 'GBPJPY']


STRATEGY_PRESETS = {
    # Baseline Smart Money setup.
    'smc_core': {
        'global': {},
        'per_symbol': {},
    },
    # Lower frequency, quality-biased profile.
    'smc_conservative': {
        'global': {
            'require_fvg': True,
            'min_score': 1,
            'no_partial': True,
            'pullback_min': 0.12,
        },
        'per_symbol': {
            'NAS100': {'min_score': 2},
            'XAUUSD': {'min_score': 1},
        },
    },
    # Higher turnover profile.
    'smc_aggressive': {
        'global': {
            'min_score': 0,
            'no_partial': False,
            'pullback_min': 0.05,
            'pullback_max': 0.96,
        },
        'per_symbol': {
            'EURUSD': {'rr': 1.4, 'atr_mult_stop': 0.9},
            'NAS100': {'rr': 1.8, 'atr_mult_stop': 0.7},
            'XAUUSD': {'rr': 2.2, 'atr_mult_stop': 0.7},
        },
    },
    # Breakout-leaning profile via wider sweep search and trend focus.
    'smc_breakout': {
        'global': {
            'min_score': 1,
            'sweep_search': 80,
            'trend_strength_min': 0.4,
        },
        'per_symbol': {
            'NAS100': {'rr': 2.4, 'trail_mult': 1.2},
            'XAUUSD': {'rr': 3.0, 'trail_mult': 2.0},
        },
    },
    # Mean-reversion flavor through contrarian mode where viable.
    'smc_mean_reversion': {
        'global': {
            'contrarian': True,
            'contrarian_style': 'tight_fade',
            'rr': 1.3,
            'no_partial': False,
            'trail_mult': 1.0,
        },
        'per_symbol': {
            'EURUSD': {'rr': 1.2},
            'GBPUSD': {'rr': 1.2},
        },
    },
}


def _apply_preset(symbol: str, preset_name: str, baseline: dict) -> dict:
    cfg = copy.deepcopy(baseline)
    preset = STRATEGY_PRESETS[preset_name]
    cfg.update(preset.get('global', {}))
    cfg.update(preset.get('per_symbol', {}).get(symbol, {}))
    return cfg


def _month_windows(df: pd.DataFrame, years: int, max_months: int = 0) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    end = df.index.max().to_period('M').to_timestamp('M')
    start = (end - pd.DateOffset(years=years)) + pd.DateOffset(days=1)
    months = pd.period_range(start=start.to_period('M'), end=end.to_period('M'), freq='M')
    windows = []
    for m in months:
        ms = m.to_timestamp(how='start')
        me = m.to_timestamp(how='end')
        windows.append((ms, me))
    if max_months and max_months > 0:
        windows = windows[-max_months:]
    return windows


def run_monthly_robustness(instruments: list[str], years: int, bars: int, risk_pct: float, presets: list[str], max_months: int = 0) -> pd.DataFrame:
    rows = []
    baseline_rules = {sym: copy.deepcopy(sm.SYMBOL_RULES.get(sym, {})) for sym in sm.SYMBOL_RULES.keys()}

    for symbol in instruments:
        try:
            df = bt.fetch_data(symbol, bars=bars)
        except Exception as exc:
            rows.append({
                'symbol': symbol,
                'preset': 'n/a',
                'month': 'n/a',
                'error': str(exc),
            })
            continue
        if df is None or len(df) < 2000:
            rows.append({'symbol': symbol, 'preset': 'n/a', 'month': 'n/a', 'error': 'insufficient_data'})
            continue

        windows = _month_windows(df, years=years, max_months=max_months)

        for preset_name in presets:
            if symbol not in baseline_rules:
                continue
            sym_cfg = _apply_preset(symbol, preset_name, baseline_rules[symbol])
            sm.SYMBOL_RULES[symbol].update(sym_cfg)
            print(f"[LAB] {symbol} | {preset_name} | months={len(windows)}")

            for ms, me in windows:
                mdf = df.loc[ms:me].copy()
                if len(mdf) < 700:
                    continue
                try:
                    result = bt.run_live_smc_engine_backtest(mdf, symbol=symbol, risk_pct=risk_pct)
                    m = result['metrics']
                    rows.append({
                        'symbol': symbol,
                        'preset': preset_name,
                        'month': ms.strftime('%Y-%m'),
                        'return_pct': float(m.get('return_pct', 0.0)),
                        'total_trades': float(m.get('total_trades', 0.0)),
                        'win_rate': float(m.get('win_rate', 0.0)),
                        'avg_r': float(m.get('avg_r', 0.0)),
                        'max_drawdown_pct': float(m.get('max_drawdown_pct', 0.0)),
                    })
                except Exception as exc:
                    rows.append({
                        'symbol': symbol,
                        'preset': preset_name,
                        'month': ms.strftime('%Y-%m'),
                        'error': str(exc),
                    })

            sm.SYMBOL_RULES[symbol].update(baseline_rules[symbol])

    return pd.DataFrame(rows)


def summarize_monthly(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    ok = df.dropna(subset=['return_pct']).copy()
    if ok.empty:
        return pd.DataFrame()
    grouped = ok.groupby(['symbol', 'preset'], as_index=False).agg(
        months=('month', 'count'),
        avg_return_pct=('return_pct', 'mean'),
        median_return_pct=('return_pct', 'median'),
        worst_month_pct=('return_pct', 'min'),
        best_month_pct=('return_pct', 'max'),
        positive_months_pct=('return_pct', lambda s: float((s > 0).mean() * 100.0)),
        avg_trades=('total_trades', 'mean'),
        avg_win_rate=('win_rate', 'mean'),
        avg_max_dd=('max_drawdown_pct', 'mean'),
    )
    grouped['robust_pass'] = (
        (grouped['months'] >= 18)
        & (grouped['positive_months_pct'] >= 55.0)
        & (grouped['avg_return_pct'] > 0.0)
        & (grouped['worst_month_pct'] > -12.0)
    )
    return grouped.sort_values(['robust_pass', 'avg_return_pct', 'positive_months_pct'], ascending=[False, False, False])


def save_outputs(monthly: pd.DataFrame, summary: pd.DataFrame, out_dir: Path) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    monthly_path = out_dir / f'monthly_robustness_{stamp}.csv'
    summary_path = out_dir / f'monthly_summary_{stamp}.csv'
    json_path = out_dir / f'monthly_summary_{stamp}.json'
    monthly.to_csv(monthly_path, index=False)
    summary.to_csv(summary_path, index=False)
    json_path.write_text(summary.to_json(orient='records', indent=2), encoding='utf-8')
    return monthly_path, summary_path, json_path


def main() -> None:
    parser = argparse.ArgumentParser(description='Long-horizon multi-strategy monthly robustness lab.')
    parser.add_argument('--instruments', type=str, default=','.join(DEFAULT_INSTRUMENTS))
    parser.add_argument('--years', type=int, default=2)
    parser.add_argument('--bars', type=int, default=120000)
    parser.add_argument('--risk-pct', type=float, default=1.0)
    parser.add_argument('--presets', type=str, default=','.join(STRATEGY_PRESETS.keys()))
    parser.add_argument('--max-months', type=int, default=0, help='Optional cap on number of most recent months to test')
    parser.add_argument('--out-dir', type=str, default='BOT/mt5_bot/liverun/research')
    args = parser.parse_args()

    instruments = [s.strip().upper() for s in args.instruments.split(',') if s.strip()]
    presets = [p.strip() for p in args.presets.split(',') if p.strip()]
    presets = [p for p in presets if p in STRATEGY_PRESETS]
    if not presets:
        raise ValueError('No valid presets selected. Use --presets with known names.')

    monthly = run_monthly_robustness(
        instruments=instruments,
        years=args.years,
        bars=args.bars,
        risk_pct=args.risk_pct,
        presets=presets,
        max_months=args.max_months,
    )
    summary = summarize_monthly(monthly)
    monthly_path, summary_path, json_path = save_outputs(monthly, summary, Path(args.out_dir))

    print('\n=== LONG HORIZON ROBUSTNESS SUMMARY ===')
    if summary.empty:
        print('No valid monthly metrics generated. Check data availability and symbols.')
    else:
        cols = ['symbol', 'preset', 'months', 'avg_return_pct', 'positive_months_pct', 'worst_month_pct', 'avg_trades', 'robust_pass']
        print(summary[cols].head(20).to_string(index=False))

    print(f'\nSaved monthly metrics: {monthly_path}')
    print(f'Saved summary csv: {summary_path}')
    print(f'Saved summary json: {json_path}')


if __name__ == '__main__':
    main()
