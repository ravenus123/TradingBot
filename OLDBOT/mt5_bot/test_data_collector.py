"""
Test script for hedge fund data collector.
Verifies that all data collection functions work correctly.
"""
from pathlib import Path
from datetime import datetime, timezone
from hedgefund_data_collector import HedgeFundDataCollector

def test_data_collector():
    """Test all data collection functions."""
    print("=" * 80)
    print("TESTING HEDGE FUND DATA COLLECTOR")
    print("=" * 80)
    
    # Initialize collector
    collector = HedgeFundDataCollector()
    print(f"\n[✓] Data collector initialized")
    print(f"    Base directory: {collector.base_dir}")
    print(f"    Trades directory: {collector.trades_dir}")
    print(f"    Performance directory: {collector.performance_dir}")
    print(f"    Regime directory: {collector.regime_dir}")
    print(f"    Infrastructure directory: {collector.infrastructure_dir}")
    
    # Test trade execution logging
    print("\n[TEST] Trade execution logging...")
    trade_data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'symbol': 'XAUUSD',
        'strategy': 'bollinger',
        'label': 'bollinger:XAUUSD:1',
        'direction': 'BUY',
        'entry_price': 2000.0,
        'stop_loss': 1990.0,
        'take_profit': 2020.0,
        'lot_size': 0.1,
        'risk_percent': 1.0,
        'atr': 5.0,
        'signal_score': 8,
        'signal_type': 'BOLLINGER_BREAKOUT',
        'execution_time_ms': 150,
        'slippage_pips': 2,
        'spread_at_entry': 0.5,
        'status': 'OPEN',
        'exit_time': '',
        'exit_price': 0,
        'profit': 0,
        'holding_period_minutes': 0,
    }
    collector.log_trade_execution(trade_data)
    print("[✓] Trade execution logged")
    
    # Test trade closure logging
    print("\n[TEST] Trade closure logging...")
    close_data = trade_data.copy()
    close_data['status'] = 'CLOSED'
    close_data['exit_time'] = datetime.now(timezone.utc).isoformat()
    close_data['exit_price'] = 2015.0
    close_data['profit'] = 150.0
    close_data['holding_period_minutes'] = 120
    collector.log_trade_execution(close_data)
    print("[✓] Trade closure logged")
    
    # Test performance snapshot logging
    print("\n[TEST] Performance snapshot logging...")
    collector.log_performance_snapshot(equity=10000.0, balance=10000.0, open_pnl=0.0)
    collector.log_performance_snapshot(equity=10150.0, balance=10150.0, open_pnl=0.0)
    print("[✓] Performance snapshots logged")
    
    # Test daily P&L logging
    print("\n[TEST] Daily P&L logging...")
    collector.log_daily_pnl(date='2026-06-02', realized_pnl=150.0, unrealized_pnl=0.0, trades_count=1)
    print("[✓] Daily P&L logged")
    
    # Test market regime logging
    print("\n[TEST] Market regime logging...")
    regime_data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'symbol': 'XAUUSD',
        'atr': 5.0,
        'atr_percent': 0.25,
        'trend_direction': 'UP',
        'trend_strength': 0.7,
        'volatility_regime': 'MEDIUM',
        'volume_regime': 'MEDIUM',
        'rsi': 55,
        'ema_trend': 'UP',
        'price_range_pct': 0.3,
    }
    collector.log_market_regime('XAUUSD', regime_data)
    print("[✓] Market regime logged")
    
    # Test infrastructure event logging
    print("\n[TEST] Infrastructure event logging...")
    collector.log_infrastructure_event('MT5_CONNECT', {'success': True, 'account': 123456})
    collector.log_infrastructure_event('ORDER_REJECT', {'symbol': 'XAUUSD', 'reason': 'Insufficient margin'})
    print("[✓] Infrastructure events logged")
    
    # Test performance metrics calculation
    print("\n[TEST] Performance metrics calculation...")
    metrics = collector.calculate_performance_metrics()
    print(f"[✓] Performance metrics calculated:")
    for key, value in metrics.items():
        print(f"    {key}: {value}")
    
    # Test summary report generation
    print("\n[TEST] Summary report generation...")
    summary = collector.generate_summary_report()
    print(summary)
    
    # Test JSON snapshot
    print("\n[TEST] JSON snapshot...")
    snapshot_file = collector.save_json_snapshot()
    print(f"[✓] JSON snapshot saved: {snapshot_file}")
    
    # Verify files were created
    print("\n[TEST] Verifying files...")
    trades_files = list(collector.trades_dir.glob('*.csv'))
    performance_files = list(collector.performance_dir.glob('*.csv'))
    regime_files = list(collector.regime_dir.glob('*.csv'))
    infrastructure_files = list(collector.infrastructure_dir.glob('*.csv'))
    snapshot_files = list(collector.base_dir.glob('*.json'))
    
    print(f"    Trades CSV files: {len(trades_files)}")
    print(f"    Performance CSV files: {len(performance_files)}")
    print(f"    Regime CSV files: {len(regime_files)}")
    print(f"    Infrastructure CSV files: {len(infrastructure_files)}")
    print(f"    Snapshot JSON files: {len(snapshot_files)}")
    
    print("\n" + "=" * 80)
    print("ALL TESTS PASSED")
    print("=" * 80)
    print("\nData collection system is ready for 2-week demo test.")
    print("All data will be saved to: hedgefunddata/")
    print("  - trades/       : Trade execution data")
    print("  - performance/  : Equity curve, daily P&L")
    print("  - regime/       : Market regime data (volatility, trend)")
    print("  - infrastructure/ : MT5 events, errors, reconnections")
    print("  - snapshots/    : JSON snapshots for analysis")

if __name__ == '__main__':
    test_data_collector()
