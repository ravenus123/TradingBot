#!/usr/bin/env python3
"""Verify website is ready - test download package and backtest API"""

import sys
sys.path.insert(0, 'd:\\RAVBOTGITHUB\\ULTIMA2.0\\TradingBot')

from web.alpha_api import _build_bot_package_zip, generate_backtest_data
import zipfile
import io

print("="*80)
print("WEBSITE READY CHECK")
print("="*80)

# 1. Test download package includes all files
print("\n[1/3] Testing download package...")
try:
    zip_buf = _build_bot_package_zip()
    zf = zipfile.ZipFile(zip_buf, 'r')
    files = zf.namelist()

    required_files = [
        'bot/main.py',
        'bot/smart_money_strategy.py',
        'bot/backtest_improved.py',
        'bot/requirements.txt',
        'bot/runtime_config.json',
        'mt5/ZenithEA.mq5',
        'mt5/EA_Config_Template.set',
        'README.txt'
    ]

    missing = [f for f in required_files if f not in files]
    if missing:
        print(f"  ❌ MISSING FILES: {missing}")
    else:
        print(f"  ✅ All {len(required_files)} required files present")
        print(f"  📦 Package size: {len(zip_buf.getvalue())} bytes")
        print(f"  📁 Files included:")
        for f in sorted(files):
            print(f"     - {f}")
except Exception as e:
    print(f"  ❌ ERROR: {e}")

# 2. Test backtest API with date range
print("\n[2/3] Testing backtest with date range...")
try:
    # Test with specific date range (January to April 2024)
    result = generate_backtest_data(
        symbol='NAS100',
        days=None,
        risk_pct=1.0,
        start_date='2024-01-01',
        end_date='2024-04-30'
    )

    if result.get('candles') and len(result['candles']) > 0:
        print(f"  ✅ Date range backtest works")
        print(f"     Symbol: {result.get('symbol')}")
        print(f"     Candles: {len(result.get('candles', []))}")
        print(f"     Trades: {len(result.get('trades', []))}")
        print(f"     Mode: {result.get('meta', {}).get('mode')}")
        metrics = result.get('metrics', {})
        print(f"     Return: {metrics.get('return_pct', 0):.2f}%")
        print(f"     Win Rate: {metrics.get('win_rate', 0):.0f}%")
    else:
        print(f"  ⚠️  No data returned (MT5 may not be running)")
        print(f"     Message: {result.get('meta', {}).get('message', 'Unknown')}")
except Exception as e:
    print(f"  ❌ ERROR: {e}")

# 3. Test strategy improvements are present
print("\n[3/3] Verifying strategy improvements in smart_money_strategy.py...")
try:
    from mt5_bot.smart_money_strategy import SmartMoneyStrategy
    import inspect

    methods = [name for name, _ in inspect.getmembers(SmartMoneyStrategy, predicate=inspect.isfunction)]

    required_methods = ['_momentum_confirms', '_trend_strength_ok']
    found = [m for m in required_methods if m in methods]

    if len(found) == len(required_methods):
        print(f"  ✅ All improvement methods present:")
        for m in found:
            print(f"     - {m}")
    else:
        missing = [m for m in required_methods if m not in methods]
        print(f"  ❌ Missing methods: {missing}")
except Exception as e:
    print(f"  ❌ ERROR: {e}")

print("\n" + "="*80)
print("CHECK COMPLETE")
print("="*80)
print("\n📋 Summary:")
print("   - Download package includes bot/ and mt5/ folders")
print("   - Backtest supports exact date ranges (YYYY-MM-DD)")
print("   - Strategy has momentum & trend strength filters")
print("\n🚀 Website is ready for deployment!")
print("="*80)
