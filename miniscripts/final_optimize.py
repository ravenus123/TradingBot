"""Fast final optimization: NAS100 + verify all 3 symbols"""
from mt5_bot.backtest_improved import run_live_smc_engine_backtest, fetch_data, calendar_walk_validate
from mt5_bot import smart_money_strategy as sms
import copy

df_nas = fetch_data('NAS100', 80000)
df_eur = fetch_data('EURUSD', 80000)
df_xau = fetch_data('XAUUSD', 25000)

BASE_NAS = {
    'rr': 1.8, 'min_score': 6, 'atr_mult_stop': 0.45,
    'min_sweep_atr': 0.03, 'max_spread_pips': 6.0,
    'trail_mult': 0.8, 'use_ob': True,
    'sessions': [(13, 20)], 'no_partial': True, 'contrarian': True,
}

def test(sym, name, rules, df):
    sms.SYMBOL_RULES[sym] = rules
    r = run_live_smc_engine_backtest(df, sym, risk_pct=1.0)
    m = r['metrics']
    print(f"  {name:<44} | ret={m['return_pct']:+7.1f}% | t={m['total_trades']:3d} | "
          f"wr={m['win_rate']:4.0f}% | dd={m['max_drawdown_pct']:4.1f}% | pf={m['profit_factor']:.2f}")
    return m

print("=" * 90)
print("NAS100 OPTIMIZATION")
print("=" * 90)
test('NAS100', "BASELINE", copy.deepcopy(BASE_NAS), df_nas)
for rr in [2.0, 2.5, 3.0]:
    v = copy.deepcopy(BASE_NAS); v['rr'] = rr
    test('NAS100', f"rr={rr}", v, df_nas)
for sc in [4, 5, 7]:
    v = copy.deepcopy(BASE_NAS); v['min_score'] = sc
    test('NAS100', f"min_score={sc}", v, df_nas)
for tm in [0.6, 1.0, 1.2, None]:
    v = copy.deepcopy(BASE_NAS); v['trail_mult'] = tm
    test('NAS100', f"trail_mult={tm}", v, df_nas)
v = copy.deepcopy(BASE_NAS); v['sessions'] = [(13, 22)]
test('NAS100', "sessions 13-22", v, df_nas)
v = copy.deepcopy(BASE_NAS); v['rr'] = 2.0; v['min_score'] = 5; v['trail_mult'] = 1.0
test('NAS100', "COMBO rr=2.0+score=5+trail=1.0", v, df_nas)
v = copy.deepcopy(BASE_NAS); v['rr'] = 2.5; v['min_score'] = 5; v['trail_mult'] = 1.2
test('NAS100', "COMBO rr=2.5+score=5+trail=1.2", v, df_nas)
v = copy.deepcopy(BASE_NAS); v['rr'] = 2.0; v['trail_mult'] = 0.6
test('NAS100', "COMBO rr=2.0+trail=0.6", v, df_nas)

sms.SYMBOL_RULES['NAS100'] = copy.deepcopy(BASE_NAS)

print("\n" + "=" * 90)
print("FINAL VERIFICATION - ALL 3 SYMBOLS (current settings)")
print("=" * 90)
for sym, df in [('EURUSD', df_eur), ('NAS100', df_nas), ('XAUUSD', df_xau)]:
    sms.SYMBOL_RULES[sym] = sms.SYMBOL_RULES[sym]  # keep current
    r = run_live_smc_engine_backtest(df, sym, risk_pct=1.0)
    m = r['metrics']
    print(f"  {sym:<8} | ret={m['return_pct']:+7.1f}% | trades={m['total_trades']:3d} | "
          f"wr={m['win_rate']:4.0f}% | dd={m['max_drawdown_pct']:4.1f}% | pf={m['profit_factor']:.2f}")
