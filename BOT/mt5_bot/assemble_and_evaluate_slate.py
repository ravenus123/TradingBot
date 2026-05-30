"""Assemble candidate slate from retested top variants, evaluate each with 200 randomized periods,
compute correlation matrix, cull highly correlated candidates, and propose a demo-forward slate.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

from OLDBOT.mt5_bot.trend_momentum import generate_trend_momentum_signal
from OLDBOT.mt5_bot.mean_reversion import generate_mean_reversion_signal
from OLDBOT.mt5_bot.breakout import generate_breakout_signal

from OLDBOT.mt5_bot.backtest_improved import fetch_data


STR_FN = {
    'trend_momentum': generate_trend_momentum_signal,
    'mean_reversion': generate_mean_reversion_signal,
    'breakout': generate_breakout_signal,
}


def simulate_returns(fn, symbol, params, periods=200, period_bars=1200, bars=25000, risk_pct=1.0):
    df = fetch_data(symbol, bars=bars)
    if df is None or len(df) < period_bars + 50:
        return []
    returns = []
    for _ in range(periods):
        start = np.random.randint(0, len(df) - period_bars - 1)
        window = df.iloc[start:start+period_bars].copy()
        # resample
        df_h1 = window.resample('1h', on='Time').agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna() if 'Time' in window.columns else window
        df_m5 = window.resample('5min', on='Time').agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna() if 'Time' in window.columns else window
        try:
            sig = fn(df_h1, df_m5, symbol, {}, params) if params else fn(df_h1, df_m5, symbol, {})
        except Exception:
            sig = None
        if not sig:
            returns.append(0.0); continue
        entry = float(sig.get('entry', df_m5['Close'].iloc[-1]))
        stop = float(sig.get('stop', entry - 0.01))
        tp = float(sig.get('tp', entry + 0.02))
        direction = sig.get('direction', 'BUY')
        highs = df_m5['High'].astype(float).values
        lows = df_m5['Low'].astype(float).values
        out = None
        for i in range(len(highs)):
            h = highs[i]; l = lows[i]
            if direction == 'BUY':
                if h >= tp: out = tp; break
                if l <= stop: out = stop; break
            else:
                if l <= tp: out = tp; break
                if h >= stop: out = stop; break
        if out is None:
            out = float(df_m5['Close'].astype(float).values[-1])
        if direction == 'BUY':
            risk_unit = entry - stop if entry - stop != 0 else 1e-6
            r = (out - entry) / risk_unit
        else:
            risk_unit = stop - entry if stop - entry != 0 else 1e-6
            r = (entry - out) / risk_unit
        returns.append(r * float(risk_pct))
    return returns


def load_candidates(path):
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    # pick top-1 per symbol/strategy from retest file
    candidates = []
    for symbol, groups in data.items():
        for strat, picks in groups.items():
            if not picks: continue
            top = picks[0]
            candidates.append({'symbol': symbol, 'strategy': strat, 'params': top.get('params', {})})
    return candidates


def compute_correlation_matrix(returns_dict):
    df = pd.DataFrame(returns_dict)
    if df.shape[1] < 2:
        return pd.DataFrame()
    corr = df.corr()
    return corr


def cull_correlated(corr, candidates, returns_dict, thresh=0.8):
    keep = set(range(len(candidates)))
    cols = list(corr.columns)
    for i in range(len(cols)):
        for j in range(i+1, len(cols)):
            v = float(corr.iloc[i,j])
            if abs(v) >= thresh:
                # compare mean returns
                a = np.mean(returns_dict[cols[i]])
                b = np.mean(returns_dict[cols[j]])
                # drop lower mean
                if a >= b:
                    keep.discard(j)
                else:
                    keep.discard(i)
    survivors = [candidates[idx] for idx in sorted(list(keep))]
    return survivors


if __name__ == '__main__':
    # Load retested top candidates
    candidates = load_candidates('OLDBOT/mt5_bot/liverun/retest_top_variants.json')
    if not candidates:
        print('No candidates found.')
        raise SystemExit(1)

    # Evaluate each candidate with 200 randomized periods
    returns_dict = {}
    labels = []
    for idx, c in enumerate(candidates):
        label = f"{c['strategy']}:{c['symbol']}:{idx}"
        labels.append(label)
        fn = STR_FN.get(c['strategy'])
        print('Evaluating', label, 'params', c['params'])
        ret = simulate_returns(fn, c['symbol'], c['params'], periods=200, period_bars=1200, bars=25000, risk_pct=1.0)
        returns_dict[label] = ret

    # Build correlation matrix from per-period returns
    corr = compute_correlation_matrix(returns_dict)
    out_dir = Path('OLDBOT/mt5_bot/liverun')
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'slate_returns.json').write_text(json.dumps({k: v for k,v in returns_dict.items()}, indent=2))
    if not corr.empty:
        (out_dir / 'slate_correlation.json').write_text(corr.round(3).to_json())
        print('Saved correlation matrix to', out_dir / 'slate_correlation.json')

    # Cull highly correlated candidates
    survivors = []
    if not corr.empty:
        survivors = cull_correlated(corr, candidates, returns_dict, thresh=0.8)
    else:
        survivors = candidates

    # Propose slate: take survivors sorted by mean return, limit to 6
    scored = []
    for idx, c in enumerate(candidates):
        label = f"{c['strategy']}:{c['symbol']}:{idx}"
        mean_r = np.mean(returns_dict.get(label, [0.0]))
        scored.append((mean_r, c, label))
    scored.sort(key=lambda x: x[0], reverse=True)
    # choose survivors intersection with top picks
    final = []
    for mean_r, c, label in scored:
        if c in survivors and len(final) < 6:
            final.append({'label': label, 'candidate': c, 'mean_return': float(mean_r)})

    # Assign simple inverse-vol weights
    weights = {}
    if final:
        vols = np.array([np.std(returns_dict[f['label']]) for f in final])
        inv = 1.0 / np.where(vols > 0, vols, 1e-6)
        w = inv / inv.sum()
        for i, f in enumerate(final):
            weights[f['label']] = float(w[i])

    out = {'final_slate': final, 'weights': weights}
    p = out_dir / 'proposed_demo_slate.json'
    p.write_text(json.dumps(out, indent=2))
    print('Saved proposed slate to', p)

    # Quick ensemble metrics
    if final:
        # compute ensemble returns as weighted sum per period
        labels_final = [f['label'] for f in final]
        returns_matrix = np.array([returns_dict[l] for l in labels_final])
        wv = np.array([weights[l] for l in labels_final])
        ensemble_returns = np.dot(wv, returns_matrix)
        avg = float(np.mean(ensemble_returns) * 100)
        vol = float(np.std(ensemble_returns) * 100)
        print(f'Ensemble avg return pct: {avg:.3f}%  vol: {vol:.3f}%')
        (out_dir / 'ensemble_metrics.json').write_text(json.dumps({'avg_return_pct': avg, 'vol_pct': vol}, indent=2))

    print('Done')
