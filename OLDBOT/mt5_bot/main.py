# live trading bot — ICT / SMC strategy engine
# Documentation: Automatizovaný obchodný systém s Python + MetaTrader 5
# State machine: IDLE → SCANNING → ZONING → MONITORING → EXECUTION → MANAGEMENT

import argparse
import csv
import json
import math
import os
import sys
import time
import threading
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

import MetaTrader5 as mt5

# Add parent directory to sys.path for absolute imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# .env support (MT5_LOGIN, TELEGRAM_BOT_TOKEN, RISK_PER_TRADE, …)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass  # python-dotenv optional; falls back to json configs

from OLDBOT.mt5_bot.portfolio_engine import (
    PortfolioOrchestrator,
    PortfolioRiskManager,
    StrategyRegistry,
    StrategySpec,
)
from OLDBOT.mt5_bot.trend_momentum import generate_trend_momentum_signal
from OLDBOT.mt5_bot.mean_reversion import generate_mean_reversion_signal
from OLDBOT.mt5_bot.volatility_strategy import generate_volatility_signal
from OLDBOT.mt5_bot.breakout_strategy import generate_breakout_signal
from OLDBOT.mt5_bot.rsi_strategy import generate_rsi_signal
from OLDBOT.mt5_bot.stochastic_strategy import generate_stochastic_signal
from OLDBOT.mt5_bot.macd_strategy import generate_macd_signal
from OLDBOT.mt5_bot.bollinger_strategy import generate_bollinger_signal
from OLDBOT.mt5_bot.telegram_bot import send_telegram_message, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# SQLite database (trades, order_blocks, logs)
from OLDBOT.mt5_bot.db import (
    init_trading_db, insert_trade, close_trade, get_daily_pnl,
    insert_order_block, mitigate_order_block, get_active_order_blocks,
    log_event, get_trades,
)

# Hedge fund data collection
from OLDBOT.mt5_bot.hedgefund_data_collector import init_data_collector, get_data_collector


# ─── State Machine ──────────────────────────────────────────────
class BotState(Enum):
    """Trading engine finite-state machine (6 states per documentation)."""
    IDLE       = "IDLE"        # Waiting for session / outside kill-zone
    SCANNING   = "SCANNING"    # Analyzing market data & indicators
    ZONING     = "ZONING"      # Mapping OB / FVG / BOS zones
    MONITORING = "MONITORING"  # Watching for pullback into OB zone
    EXECUTION  = "EXECUTION"   # Sending order to MT5
    MANAGEMENT = "MANAGEMENT"  # Managing open position (BE / trail / close)


# Global state tracker
_current_state = BotState.IDLE


def set_state(new_state: BotState):
    """Transition the bot FSM and log the change."""
    global _current_state
    if new_state != _current_state:
        _current_state = new_state


def get_state() -> BotState:
    return _current_state


def _configure_safe_console_output():
    """Avoid crashes when terminal encoding cannot print emoji/unicode symbols."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(errors='replace')
        except Exception:
            pass


_configure_safe_console_output()


def _setup_log_tee():
    """Tee stdout/stderr to bot_engine.log so the dashboard can read activity
    regardless of how the bot was launched (terminal, auto-start, etc.)."""
    log_path = Path(__file__).parent / 'liverun' / 'bot_engine.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)

    class _Tee:
        def __init__(self, original, log_file):
            self._original = original
            self._log = log_file

        def write(self, data):
            if data:
                try:
                    self._original.write(data)
                    self._original.flush()
                except Exception:
                    pass
                try:
                    self._log.write(data)
                    self._log.flush()
                except Exception:
                    pass

        def flush(self):
            try:
                self._original.flush()
            except Exception:
                pass
            try:
                self._log.flush()
            except Exception:
                pass

        def __getattr__(self, name):
            return getattr(self._original, name)

    try:
        fh = open(log_path, 'a', encoding='utf-8', errors='replace', buffering=1)
        fh.write(f"\n\n===== BOT START {datetime.now(timezone.utc).isoformat()} (direct) =====\n")
        sys.stdout = _Tee(sys.stdout, fh)
        sys.stderr = _Tee(sys.stderr, fh)
    except Exception:
        pass


_setup_log_tee()

# Config
PRODUCTION_STRATEGY_LOCK_FILE = Path(__file__).parent / 'liverun' / 'config' / 'production_strategy_lock.json'


def _default_production_strategy_lock() -> list[dict]:
    return [
        {
            'symbol': 'NAS100',
            'strategy': 'trend_momentum',
            'label': 'trend_momentum:NAS100:5',
            'params': {'h1_ema_period': 34, 'm5_ema_period': 20, 'stop_atr_mult': 1.0},
            'enabled': True,
        },
        {
            'symbol': 'EURUSD',
            'strategy': 'mean_reversion',
            'label': 'mean_reversion:EURUSD:1',
            'params': {'window': 30, 'z_threshold': 2.0, 'stop_atr_mult': 0.8},
            'enabled': True,
        },
        {
            'symbol': 'XAUUSD',
            'strategy': 'trend_momentum',
            'label': 'trend_momentum:XAUUSD:8',
            'params': {'h1_ema_period': 34, 'm5_ema_period': 12, 'stop_atr_mult': 1.0},
            'enabled': True,
        },
    ]


def _load_production_strategy_lock() -> list[dict]:
    if PRODUCTION_STRATEGY_LOCK_FILE.exists():
        try:
            payload = json.loads(PRODUCTION_STRATEGY_LOCK_FILE.read_text(encoding='utf-8'))
            strategies = payload.get('strategies', [])
            cleaned: list[dict] = []
            for item in strategies:
                symbol = str(item.get('symbol', '')).upper().strip()
                strategy = str(item.get('strategy', '')).strip()
                if not symbol or not strategy:
                    continue
                cleaned.append({
                    'symbol': symbol,
                    'strategy': strategy,
                    'label': str(item.get('label', f'{strategy}:{symbol}:locked')),
                    'params': dict(item.get('params', {})),
                    'enabled': bool(item.get('enabled', True)),
                })
            if cleaned:
                return cleaned
        except Exception:
            pass
    return _default_production_strategy_lock()


PRODUCTION_STRATEGY_LOCK = _load_production_strategy_lock()


def _get_locked_entry(symbol: str, strategy: str) -> dict | None:
    for item in PRODUCTION_STRATEGY_LOCK:
        if not bool(item.get('enabled', True)):
            continue
        if str(item.get('symbol', '')).upper() == symbol and str(item.get('strategy', '')) == strategy:
            return item
    return None


def _build_portfolio_strategies_from_lock() -> dict:
    """Build PORTFOLIO_STRATEGIES dynamically from production_strategy_lock.json.
    This ensures main.py always uses the latest hedge fund configuration."""
    strategies = {}
    
    # Strategy style mapping
    style_map = {
        'mean_reversion': 'mean_reversion',
        'rsi': 'mean_reversion',
        'stochastic': 'momentum',
        'trend_momentum': 'trend_momentum',
        'bollinger': 'mean_reversion',
        'breakout': 'breakout',
        'volatility': 'volatility',
        'macd': 'momentum',
    }
    
    # Build strategy list from lock file - include ALL variations for multi-strategy portfolio
    for item in PRODUCTION_STRATEGY_LOCK:
        if not bool(item.get('enabled', True)):
            continue
        
        strategy_name = str(item.get('strategy', '')).strip()
        symbol = str(item.get('symbol', '')).upper().strip()
        label = str(item.get('label', '')).strip()  # e.g., "bollinger:XAUUSD:1"
        
        if not strategy_name or not symbol or not label:
            continue
        
        # Use label as unique key to support multiple variations of same strategy type
        # e.g., "bollinger:XAUUSD:1", "bollinger:XAUUSD:2", etc.
        strategy_key = label.replace(':', '_')  # Convert to valid Python identifier
        
        # Add each variation as a separate strategy
        strategies[strategy_key] = {
            'enabled': True,
            'weight': 1.0,
            'style': style_map.get(strategy_name, 'unknown'),
            'asset_class': 'multi_asset',
            'symbol': symbol,
            'params': item.get('params', {}),
        }
    
    # Ensure at least some strategies are enabled
    if not strategies:
        # Fallback to default if lock file is empty
        strategies = {
            'mean_reversion_v1': {
                'enabled': True,
                'weight': 1.0,
                'style': 'mean_reversion',
                'asset_class': 'multi_asset',
            },
            'rsi_v1': {
                'enabled': True,
                'weight': 1.0,
                'style': 'mean_reversion',
                'asset_class': 'multi_asset',
            },
        }
    
    return strategies


# Build PORTFOLIO_STRATEGIES from production_strategy_lock.json
PORTFOLIO_STRATEGIES = _build_portfolio_strategies_from_lock()

# config

# Symbols to trade - strict production lock (one proven strategy per instrument)
SYMBOLS = sorted({
    str(item.get('symbol', '')).upper()
    for item in PRODUCTION_STRATEGY_LOCK
    if bool(item.get('enabled', True)) and str(item.get('symbol', '')).strip()
})

# Broker uses '.i' suffix for most symbols
BROKER_SUFFIX = {'EURUSD': '.i', 'GBPUSD': '.i', 'USDJPY': '.i', 'XAUUSD': '.i', 'EURJPY': '.i', 'BTCUSD': '', 'SP500': '', 'NAS100': ''}

# Magic Number Separation: Each strategy gets unique magic number for order tracking
STRATEGY_MAGIC_NUMBERS = {
    'mean_reversion_v1': 100002,
    'trend_momentum_v1': 100003,
    'breakout_v1': 100004,
    'volatility_breakout_v1': 100005,
    'rsi_v1': 100006,
    'stochastic_v1': 100007,
    'bollinger_v1': 100008,
    'macd_v1': 100009,
}

# Strategy constants (same as backtested)
ATR_PERIOD = 14
ADX_PERIOD = 14
LOOKBACK_BARS = 3000     # closer to 30-day M15 backtest horizon (~2880 bars)
TP_RR_RATIO = 2.5        # Take Profit at 2.5:1 risk-reward (aligned with improved backtest engine)

# Runtime config persistence
# CRITICAL: Fixed 0.2% risk per trade for 45-strategy portfolio (no runtime adjustment)
DEFAULT_RISK = 0.2  # 0.2% per trade for multi-strategy portfolio

# Track last signal to avoid spam
last_signals = {}

# Track open positions to detect closures
tracked_positions = {}

# Max hold time: from SYMBOL_RULES timeout_bars (M5 bars × 5min)
# EURUSD=192 M5 bars=960min, NAS100/XAUUSD=96 M5 bars=480min (backtest default)
MAX_HOLD_MINUTES_DEFAULT = 96 * 5   # 96 M5 bars = 480 min = 8h (backtest default)

# Order retry settings
MAX_ORDER_RETRIES = 3
RETRY_DELAY = 1.0  # seconds

# Trade logging (only closed trades)
TRADE_LOG_FILE = str(Path(__file__).parent / 'liverun' / 'live_trades.csv')
RUNTIME_STATUS_FILE = Path(__file__).parent / 'liverun' / 'runtime_status.json'

# No adaptive/learning layer: pure fixed-rule strategy execution.

# ── Remote dashboard push config ─────────────────────────────────────────
DASHBOARD_CONFIG_FILE = Path(__file__).parent / 'dashboard_push.json'

def _load_dashboard_push_config() -> dict:
    """Load WEB_API_URL + BOT_API_KEY from dashboard_push.json or env vars."""
    cfg = {
        'web_api_url': os.getenv('WEB_API_URL', '').strip().rstrip('/'),
        'bot_api_key': os.getenv('BOT_API_KEY', '').strip(),
    }
    if DASHBOARD_CONFIG_FILE.exists():
        try:
            data = json.loads(DASHBOARD_CONFIG_FILE.read_text())
            if isinstance(data, dict):
                cfg['web_api_url'] = str(data.get('web_api_url', '') or cfg['web_api_url']).strip().rstrip('/')
                cfg['bot_api_key'] = str(data.get('bot_api_key', '') or cfg['bot_api_key']).strip()
        except Exception:
            pass
    return cfg

_dashboard_cfg = _load_dashboard_push_config()
_push_session = requests.Session()
_push_session.headers.update({'Content-Type': 'application/json'})


def push_to_dashboard(endpoint: str, data: dict):
    """HTTP POST data to the remote web dashboard. Fire-and-forget (non-blocking)."""
    cfg = _dashboard_cfg
    url = cfg.get('web_api_url', '')
    key = cfg.get('bot_api_key', '')
    if not url or not key:
        return  # remote push not configured — skip silently
    try:
        _push_session.headers['X-Bot-Key'] = key
        resp = _push_session.post(f"{url}{endpoint}", json=data, timeout=8)
        if resp.status_code not in (200, 201):
            print(f"[push] {endpoint} → HTTP {resp.status_code}")
    except Exception as e:
        print(f"[push] {endpoint} error: {e}")


def _push_heartbeat_async(payload: dict):
    """Push heartbeat to dashboard in a background thread."""
    threading.Thread(target=push_to_dashboard, args=('/api/bot/push/heartbeat', payload), daemon=True).start()


def _push_trade_async(data: dict):
    """Push trade event to dashboard in a background thread."""
    threading.Thread(target=push_to_dashboard, args=('/api/bot/push/trade', data), daemon=True).start()


def _push_logs_async(lines: list):
    """Push log lines to dashboard in a background thread."""
    if lines:
        threading.Thread(target=push_to_dashboard, args=('/api/bot/push/logs', {'lines': lines}), daemon=True).start()


def update_runtime_status(**fields):
    """Persist lightweight runtime heartbeat for dashboard/API diagnostics."""
    try:
        payload = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        RUNTIME_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_STATUS_FILE.write_text(json.dumps(payload, indent=2))
        # Push to remote dashboard
        _push_heartbeat_async(payload)
    except Exception:
        pass

# trade logging

def log_trade(trade_data: dict):
    """Log trade to CSV file for live testing records."""
    file_exists = Path(TRADE_LOG_FILE).exists()
    
    with open(TRADE_LOG_FILE, 'a', newline='') as f:
        fieldnames = ['timestamp', 'symbol', 'direction', 'entry_price', 'stop_loss', 'take_profit',
                     'lot_size', 'risk_percent', 'status', 'exit_time', 'exit_price', 'profit']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
        
        writer.writerow(trade_data)


# mt5 connection

def init_mt5() -> bool:
    """Initialize MT5 connection."""
    if not mt5.initialize():
        print(f"[!] MT5 init failed: {mt5.last_error()}")
        update_runtime_status(state='error', message=f"MT5 init failed: {mt5.last_error()}")
        return False
    update_runtime_status(state='mt5_connected', message='MT5 initialized successfully')
    return True


def check_mt5_connection() -> bool:
    """Check if MT5 is still connected."""
    try:
        account_info = mt5.account_info()
        if account_info is None:
            return False
        return True
    except Exception:
        return False


def reconnect_mt5() -> bool:
    """Attempt to reconnect to MT5."""
    print("[!] MT5 connection lost. Attempting to reconnect...")
    try:
        mt5.shutdown()
        time.sleep(2)
        if mt5.initialize():
            account_info = mt5.account_info()
            if account_info:
                print(f"[✓] MT5 reconnected: Account {account_info.login}")
                
                # Log infrastructure event
                try:
                    data_collector = get_data_collector()
                    data_collector.log_infrastructure_event('MT5_RECONNECT', {
                        'success': True,
                        'account': account_info.login,
                        'server': account_info.server if account_info else 'unknown',
                    })
                except Exception as e:
                    print(f"[!] Data collector logging failed: {e}")
                
                return True
    except Exception as e:
        print(f"[!] Reconnection failed: {e}")
        
        # Log infrastructure event
        try:
            data_collector = get_data_collector()
            data_collector.log_infrastructure_event('MT5_RECONNECT_FAILED', {
                'success': False,
                'error': str(e),
            })
        except Exception:
            pass
    
    return False


def shutdown_mt5():
    """Shutdown MT5 connection."""
    mt5.shutdown()


def get_broker_symbol(symbol: str) -> str:
    """Get the broker-specific symbol name."""
    suffix = BROKER_SUFFIX.get(symbol, '')
    return f"{symbol}{suffix}"


def get_symbol_info(symbol: str) -> dict | None:
    """Get real symbol info from MT5 (tick size, digits, spread, etc.)."""
    # Check connection first
    if not check_mt5_connection():
        if not reconnect_mt5():
            return None
    
    broker_sym = get_broker_symbol(symbol)
    info = mt5.symbol_info(broker_sym)
    if info is None:
        last_error = mt5.last_error()
        print(f"[!] Symbol {broker_sym} not found: {last_error}")
        return None
    return {
        'symbol': symbol,
        'broker_symbol': broker_sym,
        'tick_size': info.trade_tick_size,
        'tick_value': info.trade_tick_value,
        'digits': info.digits,
        'spread': info.spread,
        'bid': info.bid,
        'ask': info.ask,
        'volume_min': info.volume_min,
        'volume_max': info.volume_max,
        'volume_step': info.volume_step,
        'trade_mode': info.trade_mode,
        'session_deals': getattr(info, 'session_deals', 0),
        'session_buy_orders': getattr(info, 'session_buy_orders', 0),
        'session_sell_orders': getattr(info, 'session_sell_orders', 0),
    }


def is_symbol_tradeable(sym_info: dict | None) -> bool:
    """Return True when MT5 reports the symbol can currently be traded."""
    if not sym_info:
        return False
    # Simplified check - only verify trade_mode, session checks can be unreliable
    return sym_info.get('trade_mode') == mt5.SYMBOL_TRADE_MODE_FULL


# position mgmt

def get_open_positions() -> list:
    """Get all open positions from MT5."""
    positions = mt5.positions_get()
    if positions is None:
        return []
    return list(positions)


def get_position_for_symbol(symbol: str, strategy: str | None = None) -> dict | None:
    """Check if we have an open position for this symbol.
    If strategy is provided, check only positions with that strategy's magic number.
    This allows multiple strategies to open positions on the same symbol."""
    broker_sym = get_broker_symbol(symbol)
    positions = mt5.positions_get(symbol=broker_sym)
    if positions is None or len(positions) == 0:
        return None
    
    # If strategy specified, filter by magic number
    if strategy:
        strategy_key = f"{strategy}_v1"
        magic_num = STRATEGY_MAGIC_NUMBERS.get(strategy_key)
        if magic_num:
            # Find position with matching magic number
            for pos in positions:
                if pos.magic == magic_num:
                    return {
                        'ticket': pos.ticket,
                        'symbol': symbol,
                        'broker_symbol': broker_sym,
                        'direction': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
                        'volume': pos.volume,
                        'open_price': pos.price_open,
                        'current_price': pos.price_current,
                        'sl': pos.sl,
                        'tp': pos.tp,
                        'profit': pos.profit,
                        'open_time': datetime.fromtimestamp(pos.time, tz=timezone.utc),
                        'magic': pos.magic,
                        'strategy': strategy,
                    }
            # No position found for this specific strategy
            return None
    
    # No strategy specified, return first position (backward compatibility)
    pos = positions[0]
    return {
        'ticket': pos.ticket,
        'symbol': symbol,
        'broker_symbol': broker_sym,
        'direction': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
        'volume': pos.volume,
        'open_price': pos.price_open,
        'current_price': pos.price_current,
        'sl': pos.sl,
        'tp': pos.tp,
        'profit': pos.profit,
        'open_time': datetime.fromtimestamp(pos.time, tz=timezone.utc),
        'magic': pos.magic,
    }


def calculate_lot_size(symbol: str, risk_percent: float, stop_distance: float, sym_info: dict) -> float:
    """Calculate lot size based on risk percentage and stop distance."""
    account = mt5.account_info()
    if account is None:
        return sym_info['volume_min']
    
    balance = account.equity  # use equity to match backtest compounding (equity * risk%)
    risk_amount = balance * (risk_percent / 100.0)
    
    # Get tick value (profit per 1 lot per 1 tick movement)
    tick_value = sym_info['tick_value']
    tick_size = sym_info['tick_size']
    
    if tick_value <= 0 or tick_size <= 0 or stop_distance <= 0:
        return sym_info['volume_min']
    
    # Calculate how many ticks in our stop distance
    ticks_in_stop = stop_distance / tick_size
    
    # Calculate lot size: risk_amount / (ticks * tick_value)
    lot_size = risk_amount / (ticks_in_stop * tick_value)
    
    # Round to volume step and clamp to min/max
    vol_step = sym_info['volume_step']
    lot_size = round(lot_size / vol_step) * vol_step
    lot_size = max(sym_info['volume_min'], min(lot_size, sym_info['volume_max']))
    
    # DEBUG: Print lot calculation
    print(f"[LOT] {symbol}: balance=${balance:.0f}, risk={risk_percent}% (${risk_amount:.0f}), stop_dist={stop_distance:.2f}, ticks={ticks_in_stop:.2f}, tick_val={tick_value}, → lot={lot_size}")
    
    return round(lot_size, 2)


def place_order(signal: dict, sym_info: dict, lot_size: float) -> dict:
    """Place a market order with SL. Returns result dict."""
    try:
        print(f"[LOG] Placing order: {signal.get('symbol')} {signal.get('direction')} @ {signal.get('entry')} lot={lot_size}")
        broker_sym = signal['broker_symbol']
        
        # Get magic number for this strategy
        strategy = signal.get('strategy', 'mean_reversion')
        strategy_key = f"{strategy}_v1"
        magic_num = STRATEGY_MAGIC_NUMBERS.get(strategy_key, 123456)
        
        # Safe comment: truncate to 20 chars and remove special chars
        safe_strategy = strategy.replace('_', '')[:20]
        
        # Determine order type
        if signal['direction'] == 'BUY':
            order_type = mt5.ORDER_TYPE_BUY
            price = sym_info['ask']  # fresh ask
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = sym_info['bid']  # fresh bid
        
        # Validate SL/TP distance from entry (minimum distance check)
        stop_dist = abs(signal['stop'] - price)
        tp_dist = abs(signal['tp'] - price)
        
        # Get minimum distance from symbol info
        min_distance = sym_info.get('trade_tick_size', 0.00001) * 10  # 10 ticks minimum
        
        if stop_dist < min_distance:
            print(f"[ERROR] {signal.get('symbol')}: Stop distance too small ({stop_dist:.5f} < {min_distance:.5f})")
            return {'success': False, 'error': f'Stop distance too small: {stop_dist:.5f}'}
        
        if tp_dist < min_distance:
            print(f"[ERROR] {signal.get('symbol')}: TP distance too small ({tp_dist:.5f} < {min_distance:.5f})")
            return {'success': False, 'error': f'TP distance too small: {tp_dist:.5f}'}
        
        request = {
            'action': mt5.TRADE_ACTION_DEAL,
            'symbol': broker_sym,
            'volume': lot_size,
            'type': order_type,
            'price': price,
            'sl': signal['stop'],
            'tp': signal['tp'],
            'deviation': 20,  # slippage in points
            'magic': magic_num,  # Strategy-specific magic number
            'comment': safe_strategy,
            'type_time': mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        
        if result is None:
            print(f"[ERROR] Order send returned None for {signal.get('symbol')}")
            return {'success': False, 'error': 'Order send returned None'}
        
        # Check if result has retcode attribute (it should be an object, not dict)
        if hasattr(result, 'retcode'):
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"[ERROR] Order failed for {signal.get('symbol')}: retcode={result.retcode}, comment={result.comment}")
                return {'success': False, 'error': f"Order failed: {result.comment}", 'retcode': result.retcode}
        else:
            # If result is a dict (shouldn't happen but handle it)
            if isinstance(result, dict):
                retcode = result.get('retcode', -1)
                comment = result.get('comment', 'Unknown')
                print(f"[ERROR] Order failed for {signal.get('symbol')}: retcode={retcode}, comment={comment}")
                return {'success': False, 'error': f"Order failed: {comment}", 'retcode': retcode}
            else:
                print(f"[ERROR] Unexpected result type for {signal.get('symbol')}: {type(result)}")
                return {'success': False, 'error': 'Unexpected result type'}
        
        print(f"[LOG] Order placed successfully: {signal.get('symbol')} ticket={result.order}")
        
        # Log trade execution to hedge fund data collector
        try:
            data_collector = get_data_collector()
            execution_time_ms = 0  # Could add timing if needed
            slippage_pips = 0  # Could calculate if we have expected price
            
            trade_data = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'symbol': signal.get('symbol', ''),
                'strategy': strategy,
                'label': signal.get('label', f'{strategy}:{signal.get("symbol", "")}:1'),
                'direction': signal.get('direction', ''),
                'entry_price': price,
                'stop_loss': signal.get('stop', 0),
                'take_profit': signal.get('tp', 0),
                'lot_size': lot_size,
                'risk_percent': signal.get('risk_percent', 1.0),
                'atr': signal.get('atr', 0),
                'signal_score': signal.get('score', 0),
                'signal_type': signal.get('setup', ''),
                'execution_time_ms': execution_time_ms,
                'slippage_pips': slippage_pips,
                'spread_at_entry': sym_info.get('spread', 0),
                'status': 'OPEN' if result and result.retcode == mt5.TRADE_RETCODE_DONE else 'FAILED',
                'exit_time': '',
                'exit_price': 0,
                'profit': 0,
                'holding_period_minutes': 0,
            }
            data_collector.log_trade_execution(trade_data)
        except Exception as e:
            print(f"[!] Data collector logging failed: {e}")
        
        return result
    except Exception as e:
        print(f"[ERROR] place_order failed for {signal.get('symbol')}: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}


def open_position_with_retry(signal: dict, sym_info: dict, risk_percent: float) -> bool:
    """Attempt to open position with retry logic. Returns True if successful."""
    stop_distance = abs(signal['entry'] - signal['stop'])
    lot_size = calculate_lot_size(signal['symbol'], risk_percent, stop_distance, sym_info)
    
    for attempt in range(1, MAX_ORDER_RETRIES + 1):
        # Refresh symbol info for latest prices
        fresh_info = get_symbol_info(signal['symbol'])
        if fresh_info:
            sym_info = fresh_info
        
        result = place_order(signal, sym_info, lot_size)
        
        if result is None:
            print(f"[!] Order send failed: {mt5.last_error()}")
            time.sleep(RETRY_DELAY)
            continue
        
        # Handle dict return from place_order (failure case)
        if isinstance(result, dict):
            if result.get('success'):
                # This shouldn't happen - success returns MT5 object, not dict
                fill_line = f"[✓] Order filled: {signal['direction']} {lot_size} {signal['symbol']} @ {result.get('price', 'N/A')}"
                print(fill_line)
                _push_logs_async([fill_line])
                signal['lot_size'] = lot_size
                return True
            else:
                # Order failed
                retcode = result.get('retcode', -1)
                error_msg = result.get('error', 'Unknown error')
                print(f"[!] Order failed: code={retcode}, error={error_msg}")
                
                # Check if it's a retriable error
                retriable_codes = [
                    mt5.TRADE_RETCODE_REQUOTE,
                    mt5.TRADE_RETCODE_PRICE_CHANGED,
                    mt5.TRADE_RETCODE_CONNECTION,
                ]
                
                if retcode in retriable_codes and attempt < MAX_ORDER_RETRIES:
                    print(f"[!] Order attempt {attempt} failed (code {retcode}), retrying...")
                    time.sleep(RETRY_DELAY)
                else:
                    return False
                continue
        
        # Handle MT5 result object (success case)
        if hasattr(result, 'retcode'):
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                fill_line = f"[✓] Order filled: {signal['direction']} {lot_size} {signal['symbol']} @ {result.price}"
                print(fill_line)
                _push_logs_async([fill_line])
                signal['lot_size'] = lot_size  # Store for logging
                return True
            
            # Check if it's a retriable error
            retriable_codes = [
                mt5.TRADE_RETCODE_REQUOTE,
                mt5.TRADE_RETCODE_PRICE_CHANGED,
                mt5.TRADE_RETCODE_PRICE_OFF,
                mt5.TRADE_RETCODE_TIMEOUT,
                mt5.TRADE_RETCODE_CONNECTION,
            ]
            
            if result.retcode in retriable_codes and attempt < MAX_ORDER_RETRIES:
                print(f"[!] Order attempt {attempt} failed (code {result.retcode}), retrying...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"[!] Order failed: code={result.retcode}, comment={result.comment}")
                return False
            continue
    
    return False


def close_position(position: dict, volume: float | None = None) -> bool:
    """Close an existing position (fully or partially)."""
    broker_sym = position['broker_symbol']
    ticket = position['ticket']
    close_volume = volume if volume is not None else position['volume']
    
    # Determine close direction (opposite of position)
    if position['direction'] == 'BUY':
        order_type = mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info_tick(broker_sym).bid
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(broker_sym).ask
    
    request = {
        'action': mt5.TRADE_ACTION_DEAL,
        'symbol': broker_sym,
        'volume': close_volume,
        'type': order_type,
        'position': ticket,
        'price': price,
        'deviation': 20,
        'magic': 123456,
        'comment': 'TrendBot Close',
        'type_time': mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }
    
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        action = "partial" if volume and volume < position['volume'] else "closed"
        print(f"[✓] Position {action}: {position['direction']} {close_volume} {position['symbol']}")
        
        # Log trade closure to hedge fund data collector
        try:
            data_collector = get_data_collector()
            
            # Calculate holding period
            entry_time = position.get('time', datetime.now(timezone.utc))
            exit_time = datetime.now(timezone.utc)
            holding_period_minutes = (exit_time - entry_time).total_seconds() / 60 if isinstance(entry_time, datetime) else 0
            
            # Calculate profit (from position if available)
            profit = position.get('profit', 0)
            
            trade_data = {
                'timestamp': position.get('time', datetime.now(timezone.utc)).isoformat(),
                'symbol': position.get('symbol', ''),
                'strategy': position.get('comment', '').split()[0] if position.get('comment') else 'unknown',
                'label': position.get('label', f'{position.get("symbol", "")}:closed'),
                'direction': position.get('direction', ''),
                'entry_price': position.get('price_open', 0),
                'stop_loss': position.get('sl', 0),
                'take_profit': position.get('tp', 0),
                'lot_size': close_volume,
                'risk_percent': 0,
                'atr': 0,
                'signal_score': 0,
                'signal_type': '',
                'execution_time_ms': 0,
                'slippage_pips': 0,
                'spread_at_entry': 0,
                'status': 'CLOSED',
                'exit_time': exit_time.isoformat(),
                'exit_price': price,
                'profit': profit,
                'holding_period_minutes': holding_period_minutes,
            }
            data_collector.log_trade_execution(trade_data)
        except Exception as e:
            print(f"[!] Data collector logging failed: {e}")
        
        return True
    
    print(f"[!] Close failed: {result.retcode if result else mt5.last_error()}")
    return False


def close_partial_position(position: dict, fraction: float = 0.5) -> tuple[bool, float]:
    """Close a fraction of position (e.g., 0.5 = 50%). Returns (success, closed_volume)."""
    close_volume = round(position['volume'] * fraction, 2)
    if close_volume <= 0:
        return False, 0.0
    success = close_position(position, close_volume)
    return success, close_volume if success else 0.0


def modify_position_sl_tp(position: dict, new_sl: float | None = None, new_tp: float | None = None) -> bool:
    """Modify SL/TP for an open position. Returns True if successful."""
    if new_sl is None and new_tp is None:
        return False

    broker_sym = position['broker_symbol']
    digits = get_symbol_info(position['symbol']).get('digits', 5) if get_symbol_info(position['symbol']) else 5
    request = {
        'action': mt5.TRADE_ACTION_SLTP,
        'symbol': broker_sym,
        'position': position['ticket'],
        'sl': round(new_sl, digits) if new_sl is not None else position.get('sl', 0),
        'tp': round(new_tp, digits) if new_tp is not None else position.get('tp', 0),
        'magic': 123456,
        'comment': 'TrendBot SLTP'
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return True
    print(f"[!] SL/TP modify failed: {result.retcode if result else mt5.last_error()}")
    return False


def fetch_live_candles(symbol: str, timeframe=mt5.TIMEFRAME_M15, bars: int = LOOKBACK_BARS) -> pd.DataFrame | None:
    """Fetch live candles from MT5 (M15 timeframe per documentation)."""
    try:
        print(f"[LOG] Fetching candles for {symbol}: timeframe={timeframe}, bars={bars}")
        # Check connection first
        if not check_mt5_connection():
            if not reconnect_mt5():
                print(f"[ERROR] {symbol}: MT5 connection failed")
                return None
        
        broker_sym = get_broker_symbol(symbol)
        
        # Ensure symbol is visible in Market Watch
        if not mt5.symbol_select(broker_sym, True):
            # Try once more after small delay
            time.sleep(0.5)
            if not mt5.symbol_select(broker_sym, True):
                last_error = mt5.last_error()
                print(f"[ERROR] {symbol}: Failed to select {broker_sym}: {last_error}")
                return None
        
        rates = mt5.copy_rates_from_pos(broker_sym, timeframe, 0, bars)
        if rates is None or len(rates) < 100:
            print(f"[ERROR] {symbol}: Not enough candles (got {len(rates) if rates is not None else 0})")
            return None
        
        df = pd.DataFrame(rates)
        df['Time'] = pd.to_datetime(df['time'], unit='s')
        df = df[['Time', 'open', 'high', 'low', 'close', 'tick_volume']]
        df.columns = ['Time', 'Open', 'High', 'Low', 'Close', 'Volume']
        
        print(f"[LOG] {symbol}: Fetched {len(df)} candles successfully")
        
        # Log market regime data periodically
        try:
            data_collector = get_data_collector()
            
            # Calculate ATR (volatility)
            high_low = df['High'] - df['Low']
            high_close = abs(df['High'] - df['Close'].shift())
            low_close = abs(df['Low'] - df['Close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = ranges.max(axis=1)
            atr = true_range.rolling(14).mean().iloc[-1]
            
            # Calculate ATR as percentage of price
            current_price = df['Close'].iloc[-1]
            atr_percent = (atr / current_price * 100) if current_price > 0 else 0
            
            # Determine trend direction (simple EMA crossover)
            ema_20 = df['Close'].ewm(span=20, adjust=False).mean()
            ema_50 = df['Close'].ewm(span=50, adjust=False).mean()
            if ema_20.iloc[-1] > ema_50.iloc[-1]:
                trend_direction = 'UP'
            elif ema_20.iloc[-1] < ema_50.iloc[-1]:
                trend_direction = 'DOWN'
            else:
                trend_direction = 'SIDEWAYS'
            
            # Calculate trend strength (0-1)
            trend_strength = abs(ema_20.iloc[-1] - ema_50.iloc[-1]) / current_price if current_price > 0 else 0
            trend_strength = min(trend_strength * 10, 1.0)  # Scale to 0-1
            
            # Determine volatility regime
            if atr_percent < 0.5:
                volatility_regime = 'LOW'
            elif atr_percent < 1.5:
                volatility_regime = 'MEDIUM'
            else:
                volatility_regime = 'HIGH'
            
            # Calculate daily price range as percentage
            daily_range = (df['High'].iloc[-1] - df['Low'].iloc[-1]) / current_price * 100 if current_price > 0 else 0
            
            # Log regime data
            regime_data = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'symbol': symbol,
                'atr': atr,
                'atr_percent': atr_percent,
                'trend_direction': trend_direction,
                'trend_strength': trend_strength,
                'volatility_regime': volatility_regime,
                'volume_regime': 'MEDIUM',  # Could calculate from volume
                'rsi': 50,  # Could calculate if needed
                'ema_trend': trend_direction,
                'price_range_pct': daily_range,
            }
            data_collector.log_market_regime(symbol, regime_data)
        except Exception as e:
            print(f"[!] Regime logging failed for {symbol}: {e}")
        
        return df
    except Exception as e:
        print(f"[ERROR] fetch_live_candles failed for {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None


def _resample_m15_to_tf(df_m15: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample M15 candles to another timeframe — mirrors backtest exactly.
    Backtest does: df_h1 = resample(M15, '1h'), df_m5 = resample(M15, '5min')
    We do the same so live signal inputs are identical to backtest inputs."""
    work = df_m15.copy()
    # Set DatetimeIndex if not already set
    if not isinstance(work.index, pd.DatetimeIndex):
        work = work.set_index(pd.to_datetime(work['Time']))
    return work.resample(rule).agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
    }).dropna()


def fetch_m15_and_resample(symbol: str, bars: int = LOOKBACK_BARS) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Fetch M15 data and resample into H1 + M5 — exact backtest methodology.
    Returns (df_h1, df_m5) both derived from the same M15 feed.
    H1 drops the last (currently forming) bar — matches backtest h1_cutoff = decision_time - 1h
    which ensures only completed H1 bars feed into bias calculation (no lookahead)."""
    try:
        print(f"[LOG] Fetching M15 and resampling for {symbol}")
        df_m15 = fetch_live_candles(symbol, timeframe=mt5.TIMEFRAME_M15, bars=bars)
        if df_m15 is None or len(df_m15) < 100:
            print(f"[ERROR] {symbol}: Not enough M15 candles")
            return None, None
        df_h1 = _resample_m15_to_tf(df_m15, '1h').iloc[:-1]  # drop forming bar — matches backtest
        df_m5 = _resample_m15_to_tf(df_m15, '5min').iloc[:-1]  # drop forming M5 bar too
        if len(df_h1) < 80 or len(df_m5) < 80:
            print(f"[ERROR] {symbol}: Not enough resampled candles (H1={len(df_h1)}, M5={len(df_m5)})")
            return None, None
        print(f"[LOG] {symbol}: Resampled successfully (H1={len(df_h1)}, M5={len(df_m5)})")
        return df_h1, df_m5
    except Exception as e:
        print(f"[ERROR] fetch_m15_and_resample failed for {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None, None


# multi-tf context

def get_htf_bias(symbol: str, params: dict, timeframe = mt5.TIMEFRAME_H1) -> str:
    """Determine higher timeframe bias using EMAs and ADX.
    Returns one of: 'BULL', 'BEAR', 'FLAT', 'UNKNOWN'
    """
    try:
        df_htf = fetch_live_candles(symbol, timeframe=timeframe, bars=400)
        if df_htf is None or len(df_htf) < 100:
            return 'UNKNOWN'

        fast = int(params.get('EMA_Fast', 10))
        slow = int(params.get('EMA_Slow', 50))
        adx_th = float(params.get('ADX', 20))

        df_i = add_indicators(df_htf, fast, slow).fillna(0)
        i = len(df_i) - 1
        ema_f = float(df_i['EMA_Fast'].iat[i])
        ema_s = float(df_i['EMA_Slow'].iat[i])
        adx = float(df_i['ADX'].iat[i])

        # Slightly relaxed ADX threshold for HTF to get bias more often
        htf_adx_th = max(15.0, adx_th * 0.8)

        if not np.isfinite(adx):
            return 'UNKNOWN'

        if adx > htf_adx_th:
            if ema_f > ema_s:
                return 'BULL'
            elif ema_f < ema_s:
                return 'BEAR'
        return 'FLAT'
    except Exception:
        return 'UNKNOWN'


# indicators

def add_indicators(df: pd.DataFrame, fast_ema: int, slow_ema: int) -> pd.DataFrame:
    """Add EMA, ATR, ADX indicators."""
    df = df.copy()
    close = df['Close'].astype(float).values
    high = df['High'].astype(float).values
    low = df['Low'].astype(float).values

    # EMAs
    close_series = pd.Series(close)
    df['EMA_Fast'] = close_series.ewm(span=fast_ema, adjust=False).mean().values
    df['EMA_Slow'] = close_series.ewm(span=slow_ema, adjust=False).mean().values

    # ATR
    high_low = high - low
    high_close = np.abs(high - np.roll(close, 1))
    low_close = np.abs(low - np.roll(close, 1))
    tr = np.max(np.stack([high_low, high_close, low_close]), axis=0)
    df['ATR'] = pd.Series(tr).rolling(ATR_PERIOD).mean().values

    # ADX
    plus_dm = np.maximum(high - np.roll(high, 1), 0)
    minus_dm = np.maximum(np.roll(low, 1) - low, 0)
    atr = df['ATR'].to_numpy()
    atr_safe = np.where(atr > 0, atr, np.nan)
    di_plus = 100 * (pd.Series(plus_dm).rolling(ADX_PERIOD).mean().values / atr_safe)
    di_minus = 100 * (pd.Series(minus_dm).rolling(ADX_PERIOD).mean().values / atr_safe)
    dx = 100 * (np.abs(di_plus - di_minus) / np.where((di_plus + di_minus) != 0, (di_plus + di_minus), np.nan))
    df['ADX'] = pd.Series(dx).rolling(ADX_PERIOD).mean().values

    # RSI
    delta = close_series.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(50).values

    return df


def _build_portfolio_orchestrator(context: dict) -> PortfolioOrchestrator:
    """Build per-symbol strategy orchestrator with pluggable strategy modules."""

    def _mean_reversion_generator(sym: str, ctx: dict) -> dict | None:
        # ── Position check DISABLED (monte_carlo test has no position checks) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: no position checks - allows multiple positions
        # main.py: position check - DISABLED
        # if get_position_for_symbol(sym, 'mean_reversion'):
        #     return None
        # ── lock_entry check DISABLED (monte_carlo test has no lock_entry check) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: no lock_entry check
        # main.py: lock_entry check - DISABLED
        # lock_entry = _get_locked_entry(sym, 'mean_reversion')
        # if lock_entry is None:
        #     return None
        # Use default params instead of lock_entry
        params = {'EMA_Fast': 9, 'EMA_Slow': 21, 'ADX': 20, 'ATR_Mult': 1.5, 'RR': 2.0}
        signal = generate_mean_reversion_signal(
            ctx['df_1h'],
            ctx['df_5m'],
            sym,
            ctx['sym_info'],
            params=params,
        )
        if signal:
            signal['strategy'] = 'mean_reversion'
        return signal

    def _trend_momentum_generator(sym: str, ctx: dict) -> dict | None:
        # ── Position check DISABLED (monte_carlo test has no position checks) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: no position checks - allows multiple positions
        # main.py: position check - DISABLED
        # if get_position_for_symbol(sym, 'trend_momentum'):
        #     return None
        # ── lock_entry check DISABLED (monte_carlo test has no lock_entry check) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: no lock_entry check
        # main.py: lock_entry check - DISABLED
        # lock_entry = _get_locked_entry(sym, 'trend_momentum')
        # if lock_entry is None:
        #     return None
        # Use default params instead of lock_entry
        params = {'EMA_Fast': 9, 'EMA_Slow': 21, 'ADX': 20, 'ATR_Mult': 1.5, 'RR': 2.0}
        signal = generate_trend_momentum_signal(
            ctx['df_1h'],
            ctx['df_5m'],
            sym,
            ctx['sym_info'],
            params=params,
        )
        if signal:
            signal['strategy'] = 'trend_momentum'
        return signal

    def _volatility_breakout_generator(sym: str, ctx: dict) -> dict | None:
        # FINAL PORTFOLIO: DISABLE volatility_breakout (losing in walk-forward)
        # EURUSD: -4.05%, NAS100: 0.13% (flat), XAUUSD: -5.48%
        return None

    def _rsi_generator(sym: str, ctx: dict) -> dict | None:
        # ── Position check DISABLED (monte_carlo test has no position checks) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: no position checks - allows multiple positions
        # main.py: position check - DISABLED
        # if get_position_for_symbol(sym, 'rsi'):
        #     return None
        # ── lock_entry check DISABLED (monte_carlo test has no lock_entry check) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: no lock_entry check
        # main.py: lock_entry check - DISABLED
        # lock_entry = _get_locked_entry(sym, 'rsi')
        # if lock_entry is None:
        #     return None
        # Use default params instead of lock_entry
        params = {'RSI_Period': 14, 'RSI_Overbought': 70, 'RSI_Oversold': 30, 'ATR_Mult': 1.5, 'RR': 2.0}
        signal = generate_rsi_signal(
            ctx['df_1h'],
            ctx['df_5m'],
            sym,
            ctx['sym_info'],
            params=params,
        )
        if signal:
            signal['strategy'] = 'rsi'
        return signal

    def _stochastic_generator(sym: str, ctx: dict) -> dict | None:
        # ── Position check DISABLED (monte_carlo test has no position checks) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: no position checks - allows multiple positions
        # main.py: position check - DISABLED
        # if get_position_for_symbol(sym, 'stochastic'):
        #     return None
        # ── lock_entry check DISABLED (monte_carlo test has no lock_entry check) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: no lock_entry check
        # main.py: lock_entry check - DISABLED
        # lock_entry = _get_locked_entry(sym, 'stochastic')
        # if lock_entry is None:
        #     return None
        # Use default params instead of lock_entry
        params = {'Stoch_Period': 14, 'Stoch_K': 3, 'Stoch_D': 3, 'ATR_Mult': 1.5, 'RR': 2.0}
        signal = generate_stochastic_signal(
            ctx['df_1h'],
            ctx['df_5m'],
            sym,
            ctx['sym_info'],
            params=params,
        )
        if signal:
            signal['strategy'] = 'stochastic'
        return signal

    def _breakout_generator(sym: str, ctx: dict) -> dict | None:
        # ── Position check DISABLED (monte_carlo test has no position checks) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: no position checks - allows multiple positions
        # main.py: position check - DISABLED
        # if get_position_for_symbol(sym, 'breakout'):
        #     return None
        # ── lock_entry check DISABLED (monte_carlo test has no lock_entry check) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: no lock_entry check
        # main.py: lock_entry check - DISABLED
        # lock_entry = _get_locked_entry(sym, 'breakout')
        # if lock_entry is None:
        #     return None
        # Use default params instead of lock_entry
        params = {'Breakout_Period': 20, 'ATR_Mult': 1.5, 'RR': 2.0}
        signal = __import__('breakout_strategy').generate_breakout_signal(
            ctx['df_1h'],
            ctx['df_5m'],
            sym,
            ctx['sym_info'],
            params=params,
        )
        if signal:
            signal['strategy'] = 'breakout'
        return signal

    def _bollinger_generator(sym: str, ctx: dict) -> dict | None:
        # Check if this strategy already has an open position
        if get_position_for_symbol(sym, 'bollinger'):
            return None
        lock_entry = _get_locked_entry(sym, 'bollinger')
        if lock_entry is None:
            return None
        params = dict(lock_entry.get('params', {}))
        signal = __import__('bollinger_strategy').generate_bollinger_signal(
            ctx['df_1h'],
            ctx['df_5m'],
            sym,
            ctx['sym_info'],
            params=params,
        )
        if signal:
            signal['strategy'] = 'bollinger'
        return signal

    def _disabled_placeholder(_sym: str, _ctx: dict) -> dict | None:
        return None

    # Build generators dynamically for all 45 strategy variations from lock file
    generators = {}
    
    for item in PRODUCTION_STRATEGY_LOCK:
        if not bool(item.get('enabled', True)):
            continue
        
        strategy_name = str(item.get('strategy', '')).strip()
        symbol = str(item.get('symbol', '')).upper().strip()
        label = str(item.get('label', '')).strip()
        params = item.get('params', {})
        
        if not strategy_name or not symbol or not label:
            continue
        
        strategy_key = label.replace(':', '_')
        
        # Create generator function for this specific variation with its params
        if strategy_name == 'bollinger':
            def make_generator(p):
                def gen(sym: str, ctx: dict) -> dict | None:
                    try:
                        signal = __import__('bollinger_strategy').generate_bollinger_signal(
                            ctx['df_1h'],
                            ctx['df_5m'],
                            sym,
                            ctx['sym_info'],
                            params=p,
                        )
                        if signal:
                            signal['strategy'] = f'bollinger_{p}'
                            signal['broker_symbol'] = ctx['sym_info']['broker_symbol']
                            print(f"[LOG] {sym}: bollinger signal generated: {signal.get('direction')} @ {signal.get('entry')}")
                        return signal
                    except Exception as e:
                        print(f"[ERROR] {sym}: bollinger generator failed: {e}")
                        import traceback
                        traceback.print_exc()
                        return None
                return gen
            generators[strategy_key] = make_generator(params)
        
        elif strategy_name == 'volatility':
            def make_generator(p):
                def gen(sym: str, ctx: dict) -> dict | None:
                    try:
                        signal = __import__('volatility_strategy').generate_volatility_signal(
                            ctx['df_1h'],
                            ctx['df_5m'],
                            sym,
                            ctx['sym_info'],
                            params=p,
                        )
                        if signal:
                            signal['strategy'] = f'volatility_{p}'
                            signal['broker_symbol'] = ctx['sym_info']['broker_symbol']
                            print(f"[LOG] {sym}: volatility signal generated: {signal.get('direction')} @ {signal.get('entry')}")
                        return signal
                    except Exception as e:
                        print(f"[ERROR] {sym}: volatility generator failed: {e}")
                        import traceback
                        traceback.print_exc()
                        return None
                return gen
            generators[strategy_key] = make_generator(params)
        
        elif strategy_name == 'macd':
            def make_generator(p):
                def gen(sym: str, ctx: dict) -> dict | None:
                    try:
                        signal = __import__('macd_strategy').generate_macd_signal(
                            ctx['df_1h'],
                            ctx['df_5m'],
                            sym,
                            ctx['sym_info'],
                            params=p,
                        )
                        if signal:
                            signal['strategy'] = f'macd_{p}'
                            signal['broker_symbol'] = ctx['sym_info']['broker_symbol']
                            print(f"[LOG] {sym}: macd signal generated: {signal.get('direction')} @ {signal.get('entry')}")
                        return signal
                    except Exception as e:
                        print(f"[ERROR] {sym}: macd generator failed: {e}")
                        import traceback
                        traceback.print_exc()
                        return None
                return gen
            generators[strategy_key] = make_generator(params)
        
        elif strategy_name == 'mean_reversion':
            def make_generator(p):
                def gen(sym: str, ctx: dict) -> dict | None:
                    try:
                        signal = __import__('mean_reversion').generate_mean_reversion_signal(
                            ctx['df_1h'],
                            ctx['df_5m'],
                            sym,
                            ctx['sym_info'],
                            params=p,
                        )
                        if signal:
                            signal['strategy'] = f'mean_reversion_{p}'
                            signal['broker_symbol'] = ctx['sym_info']['broker_symbol']
                            print(f"[LOG] {sym}: mean_reversion signal generated: {signal.get('direction')} @ {signal.get('entry')}")
                        return signal
                    except Exception as e:
                        print(f"[ERROR] {sym}: mean_reversion generator failed: {e}")
                        import traceback
                        traceback.print_exc()
                        return None
                return gen
            generators[strategy_key] = make_generator(params)
        
        elif strategy_name == 'trend_momentum':
            def make_generator(p):
                def gen(sym: str, ctx: dict) -> dict | None:
                    try:
                        signal = __import__('trend_momentum').generate_trend_momentum_signal(
                            ctx['df_1h'],
                            ctx['df_5m'],
                            sym,
                            ctx['sym_info'],
                            params=p,
                        )
                        if signal:
                            signal['strategy'] = f'trend_momentum_{p}'
                            signal['broker_symbol'] = ctx['sym_info']['broker_symbol']
                            print(f"[LOG] {sym}: trend_momentum signal generated: {signal.get('direction')} @ {signal.get('entry')}")
                        return signal
                    except Exception as e:
                        print(f"[ERROR] {sym}: trend_momentum generator failed: {e}")
                        import traceback
                        traceback.print_exc()
                        return None
                return gen
            generators[strategy_key] = make_generator(params)
        
        elif strategy_name == 'rsi':
            def make_generator(p):
                def gen(sym: str, ctx: dict) -> dict | None:
                    try:
                        signal = __import__('rsi_strategy').generate_rsi_signal(
                            ctx['df_1h'],
                            ctx['df_5m'],
                            sym,
                            ctx['sym_info'],
                            params=p,
                        )
                        if signal:
                            signal['strategy'] = f'rsi_{p}'
                            signal['broker_symbol'] = ctx['sym_info']['broker_symbol']
                            print(f"[LOG] {sym}: rsi signal generated: {signal.get('direction')} @ {signal.get('entry')}")
                        return signal
                    except Exception as e:
                        print(f"[ERROR] {sym}: rsi generator failed: {e}")
                        import traceback
                        traceback.print_exc()
                        return None
                return gen
            generators[strategy_key] = make_generator(params)
        
        elif strategy_name == 'stochastic':
            def make_generator(p):
                def gen(sym: str, ctx: dict) -> dict | None:
                    try:
                        signal = __import__('stochastic_strategy').generate_stochastic_signal(
                            ctx['df_1h'],
                            ctx['df_5m'],
                            sym,
                            ctx['sym_info'],
                            params=p,
                        )
                        if signal:
                            signal['strategy'] = f'stochastic_{p}'
                            signal['broker_symbol'] = ctx['sym_info']['broker_symbol']
                            print(f"[LOG] {sym}: stochastic signal generated: {signal.get('direction')} @ {signal.get('entry')}")
                            return signal
                    except Exception as e:
                        print(f"[ERROR] {sym}: stochastic generator failed: {e}")
                        import traceback
                        traceback.print_exc()
                        return None
                return gen
            generators[strategy_key] = make_generator(params)
        
        elif strategy_name == 'breakout':
            def make_generator(p):
                def gen(sym: str, ctx: dict) -> dict | None:
                    try:
                        signal = __import__('breakout_strategy').generate_breakout_signal(
                            ctx['df_5m'],
                            sym,
                            ctx['sym_info'],
                            params=p,
                        )
                        if signal:
                            signal['strategy'] = f'breakout_{p}'
                            signal['broker_symbol'] = ctx['sym_info']['broker_symbol']
                            print(f"[LOG] {sym}: breakout signal generated: {signal.get('direction')} @ {signal.get('entry')}")
                        return signal
                    except Exception as e:
                        print(f"[ERROR] {sym}: breakout generator failed: {e}")
                        import traceback
                        traceback.print_exc()
                        return None
                return gen
            generators[strategy_key] = make_generator(params)

    registry = StrategyRegistry()
    for name, cfg in PORTFOLIO_STRATEGIES.items():
        registry.register(
            StrategySpec(
                name=name,
                style=str(cfg.get('style', 'unknown')),
                asset_class=str(cfg.get('asset_class', 'multi_asset')),
                weight=float(cfg.get('weight', 1.0)),
                enabled=bool(cfg.get('enabled', False)),
                generator=generators.get(name, _disabled_placeholder),
            )
        )

    # CRITICAL: 0.2% risk per trade for 45-strategy portfolio (was 1.0% for single strategy)
    # With 45 strategies, 0.2% per trade = max 9% total if all signal at once
    risk_manager = PortfolioRiskManager(max_risk_per_trade_pct=0.2, max_open_trades=50)
    return PortfolioOrchestrator(registry=registry, risk_manager=risk_manager)


# runtime config

def load_config() -> dict:
    """Load runtime config from file."""
    cfg = {
        'risk_percent': DEFAULT_RISK,
        'enabled_symbols': SYMBOLS.copy(),
    }
    cfg['enabled_symbols'] = [s for s in cfg.get('enabled_symbols', SYMBOLS) if s in SYMBOLS]

    # Reload remote dashboard push config on each scan cycle
    global _dashboard_cfg
    _dashboard_cfg = _load_dashboard_push_config()

    return cfg


# telegram

class TelegramBot:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN', TELEGRAM_BOT_TOKEN)
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', TELEGRAM_CHAT_ID)
        self.offset = None

    def is_configured(self) -> bool:
        return (self.token not in (None, '', 'YOUR_BOT_TOKEN_HERE') and 
                self.chat_id not in (None, '', 'YOUR_CHAT_ID_HERE'))

    def poll_commands(self, cfg: dict) -> dict:
        """Poll Telegram for commands and update config."""
        if not self.is_configured():
            return cfg
        try:
            params = {'timeout': 0}
            if self.offset:
                params['offset'] = self.offset
            resp = requests.get(f"https://api.telegram.org/bot{self.token}/getUpdates", 
                              params=params, timeout=3)
            data = resp.json()
            if not data.get('ok'):
                return cfg
            
            changed = False
            for upd in data.get('result', []):
                self.offset = upd['update_id'] + 1
                msg = upd.get('message', {})
                if str(msg.get('chat', {}).get('id', '')) != str(self.chat_id):
                    continue
                
                text = (msg.get('text') or '').strip().lower()
                
                # /start or /help command
                if text in ['/start', '/help']:
                    help_msg = (
                        "🤖 Ultima Trading Bot\n"
                        "--------------------------------\n"
                        "📊 Trading Commands\n"
                        "/risk [0.10-2.00] - Set risk % per trade\n"
                        "/positions - View open positions\n"
                        "/status - Bot status & settings\n"
                        "/ping - Check bot connectivity\n\n"
                        "🔍 Debugging\n"
                        "/debug <symbol> - Detailed indicators\n"
                        "/why <symbol> - Quick verdict\n\n"
                        "🔧 Symbol Toggles\n"
                        "/eurusd /gbpusd /usdjpy /xauusd /gbpjpy /btcusd /nas100\n\n"
                        "📈 Scans every 30s (ICT/SMC strategy)"
                    )
                    sent = send_telegram_message(help_msg, silent=False)
                    if not sent:
                        print("[!] Failed to send /help message to Telegram")
                
                # /debug <symbol> — detailed indicators and reason
                elif text.startswith('/debug '):
                    parts = text.split()
                    if len(parts) >= 2:
                        sym = parts[1].upper()
                        if sym in SYMBOLS:
                            sym_info = get_symbol_info(sym)
                            df = fetch_live_candles(sym)
                            params = {
                                'EMA_Fast': 20, 'EMA_Slow': 50,
                                'ADX': 20.0,
                                'ATR_Mult': 0.65,
                                'RR': 2.0,
                            }
                            if not sym_info or df is None or params is None or len(df) < 100:
                                send_telegram_message(f"❌ Not enough data for {sym}")
                            else:
                                fast = int(params.get('EMA_Fast', 10))
                                slow = int(params.get('EMA_Slow', 50))
                                adx_th = float(params.get('ADX', 20))
                                atr_mult = float(params.get('ATR_Mult', 1.5))
                                df_i = add_indicators(df, fast, slow).fillna(0)
                                i = len(df_i) - 1
                                ema_f = float(df_i['EMA_Fast'].iat[i])
                                ema_s = float(df_i['EMA_Slow'].iat[i])
                                adx = float(df_i['ADX'].iat[i])
                                atr = float(df_i['ATR'].iat[i])
                                hour = int(df_i['Time'].iat[i].hour)
                                bid = sym_info['bid']; ask = sym_info['ask']
                                spread = sym_info['spread']
                                direction = 'BUY' if ema_f > ema_s else ('SELL' if ema_f < ema_s else 'FLAT')
                                reason = []
                                if not in_session_kill_zone(hour):
                                    reason.append(f"Outside kill zone ({hour} UTC)")
                                if not np.isfinite(adx) or adx <= adx_th:
                                    reason.append(f"ADX {adx:.1f} ≤ {adx_th}")
                                if direction == 'FLAT':
                                    reason.append("EMAs equal / no trend")
                                # Would we signal?
                                sig = generate_signal(df, params, sym, sym_info)
                                would = 'YES' if sig else 'NO'
                                because = 'OK' if would == 'YES' else (', '.join(reason) or 'No condition met')
                                msg_lines = [
                                    f"🔎 <b>DEBUG {sym}</b>",
                                    "━━━━━━━━━━━━━━━━",
                                    f"EMA Fast/Slow: {ema_f:.5f} / {ema_s:.5f}",
                                    f"ADX: {adx:.1f}  | ATR: {atr:.5f}",
                                    f"Bid/Ask: {bid} / {ask}  | Spread: {spread} pts",
                                    f"Direction: {direction}",
                                    f"Session OK: {'YES' if in_session_kill_zone(hour) else 'NO'}",
                                    f"Would Signal Now: {would}",
                                    f"Reason: {because}"
                                ]
                                send_telegram_message('\n'.join(msg_lines))
                        else:
                            send_telegram_message(f"❌ Unknown symbol: {sym}")
                    else:
                        send_telegram_message("Usage: /debug <symbol>  e.g., /debug xauusd")

                # /why <symbol> — one-line verdict
                elif text.startswith('/why '):
                    parts = text.split()
                    if len(parts) >= 2:
                        sym = parts[1].upper()
                        if sym in SYMBOLS:
                            sym_info = get_symbol_info(sym)
                            df = fetch_live_candles(sym)
                            params = {
                                'EMA_Fast': 20, 'EMA_Slow': 50,
                                'ADX': 20.0,
                                'ATR_Mult': 0.65,
                                'RR': 2.0,
                            }
                            if not sym_info or df is None or params is None or len(df) < 100:
                                send_telegram_message(f"❌ {sym}: not enough data")
                            else:
                                fast = int(params.get('EMA_Fast', 10))
                                slow = int(params.get('EMA_Slow', 50))
                                adx_th = float(params.get('ADX', 20))
                                df_i = add_indicators(df, fast, slow).fillna(0)
                                i = len(df_i) - 1
                                ema_f = float(df_i['EMA_Fast'].iat[i])
                                ema_s = float(df_i['EMA_Slow'].iat[i])
                                adx = float(df_i['ADX'].iat[i])
                                hour = int(df_i['Time'].iat[i].hour)
                                reason = None
                                if not in_session_kill_zone(hour):
                                    reason = f"outside kill zone (UTC {hour})"
                                elif not np.isfinite(adx) or adx <= adx_th:
                                    reason = f"adx {adx:.1f} ≤ {adx_th}"
                                elif abs(ema_f - ema_s) < 1e-12:
                                    reason = "emas equal/no trend"
                                sig = generate_signal(df, params, sym, sym_info)
                                if sig and not reason:
                                    send_telegram_message(f"✅ {sym}: signal ready ({sig['direction']})")
                                else:
                                    send_telegram_message(f"⏸️ {sym}: no signal — {reason or 'no condition met'}")
                        else:
                            send_telegram_message(f"❌ Unknown symbol: {sym}")
                    else:
                        send_telegram_message("Usage: /why <symbol>  e.g., /why xauusd")
                
                # /ping command
                elif text == '/ping':
                    send_telegram_message("✅ Bot is online and connected.")

                # /status command
                elif text == '/status':
                    enabled = ', '.join(cfg['enabled_symbols'])
                    positions = get_open_positions()
                    pos_count = len(positions)
                    total_pl = sum(p.profit for p in positions)
                    daily_pnl = get_daily_pnl()
                    state_name = get_state().value
                    
                    status_msg = (
                        "📊 <b>Bot Status</b>\n"
                        "━━━━━━━━━━━━━━━━\n"
                        f"🔄 State: {state_name}\n"
                        f"⚠️ Risk: {cfg['risk_percent']}% per trade\n"
                        f"✅ Active Symbols: {len(cfg['enabled_symbols'])}/{len(SYMBOLS)}\n"
                        f"   {enabled}\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"📈 Open Positions: {pos_count}\n"
                    )
                    if pos_count > 0:
                        status_msg += f"💰 Open P/L: ${total_pl:.2f}\n"
                    status_msg += f"📅 Daily P&amp;L: ${daily_pnl:.2f}"
                    send_telegram_message(status_msg)
                
                # /positions command
                elif text == '/positions':
                    positions = get_open_positions()
                    if not positions:
                        send_telegram_message("📈 <b>No Open Positions</b>\n\nWaiting for signals...")
                    else:
                        lines = [f"📈 <b>Open Positions ({len(positions)})</b>\n━━━━━━━━━━━━━━━━"]
                        total_pl = 0
                        for pos in positions:
                            direction = 'BUY 🟢' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL 🔴'
                            pl_emoji = '✅' if pos.profit >= 0 else '❌'
                            lines.append(
                                f"\n<b>{pos.symbol}</b> {direction}\n"
                                f"   Lot: {pos.volume} @ {pos.price_open}\n"
                                f"   {pl_emoji} P/L: ${pos.profit:.2f}"
                            )
                            total_pl += pos.profit
                        lines.append(f"\n━━━━━━━━━━━━━━━━\n💰 <b>Total: ${total_pl:.2f}</b>")
                        send_telegram_message('\n'.join(lines))
                
                # Symbol toggle commands
                elif text.startswith('/'):
                    sym = text[1:].upper()
                    if sym in SYMBOLS:
                        enabled = set(cfg['enabled_symbols'])
                        if sym in enabled:
                            enabled.remove(sym)
                            remaining = len(enabled)
                            send_telegram_message(
                                f"🔴 <b>Disabled {sym}</b>\n"
                                f"Active symbols: {remaining}/{len(SYMBOLS)}"
                            )
                        else:
                            enabled.add(sym)
                            send_telegram_message(
                                f"🟢 <b>Enabled {sym}</b>\n"
                                f"Active symbols: {len(enabled)}/{len(SYMBOLS)}"
                            )
                        cfg['enabled_symbols'] = sorted(enabled)
                        changed = True
                    else:
                        send_telegram_message(
                            f"❌ Unknown command: {text}\n\n"
                            f"Type /help for available commands"
                        )
            
            if changed:
                save_config(cfg)
            return cfg
        except Exception:
            return cfg


def log_trade(trade_data: dict):
    """Log trade to CSV file."""
    file_exists = Path(TRADE_LOG_FILE).exists()
    
    with open(TRADE_LOG_FILE, 'a', newline='') as f:
        fieldnames = ['timestamp', 'symbol', 'direction', 'entry_price', 'stop_loss', 'take_profit',
                     'lot_size', 'risk_percent', 'status', 'exit_time', 'exit_price', 'profit']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
        
        writer.writerow(trade_data)


def send_signal_alert(signal: dict, risk: float, quiet: bool = False):
    """Send signal alert to Telegram."""
    ts = signal['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
    direction_emoji = "🟢" if signal['direction'] == 'BUY' else "🔴"
    
    msg = (
        f"{direction_emoji} <b>{signal['direction']} {signal['symbol']}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📍 Entry: {signal['entry']}\n"
        f"🎯 TP: {signal['tp']}\n"
        f"🛑 SL: {signal['stop']}\n"
        f"📊 Spread: {signal['spread']} pts\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"ADX: {signal['adx']} | ATR: {signal['atr']}\n"
        f"EMA: {signal['ema_fast']} / {signal['ema_slow']}\n"
        f"⚠️ Risk: {risk}%\n"
        f"🕐 {ts}\n"
        f"<i>{signal['params']}</i>"
    )
    
    try:
        send_telegram_message(msg)
    except Exception:
        if not quiet:
            print(f"[!] Telegram send failed")


# main loop

def show_open_positions():
    """Display all open positions on startup."""
    positions = get_open_positions()
    if not positions:
        print("[i] No open positions")
        return
    
    print(f"[i] Open positions: {len(positions)}")
    for pos in positions:
        direction = 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL'
        print(f"    {pos.symbol}: {direction} {pos.volume} @ {pos.price_open} (P/L: ${pos.profit:.2f})")


def process_single_symbol(symbol: str, enabled: set, risk: float) -> tuple:
    """Process a single symbol and return its status and ALL signals."""
    global last_signals, tracked_positions
    
    print(f"[LOG] Processing symbol: {symbol}")
    
    if symbol not in enabled:
        print(f"[LOG] {symbol}: Not in enabled symbols, skipping")
        return (symbol, None, [])
    
    # Get REAL symbol info from MT5
    sym_info = get_symbol_info(symbol)
    if not sym_info:
        print(f"[ERROR] {symbol}: Failed to get symbol info")
        return (symbol, f"{symbol}:ERR", [])
    
    # For hedge fund portfolio: DO NOT check for existing positions here
    # The portfolio orchestrator handles per-strategy position checking
    # Multiple strategies can have positions on the same symbol simultaneously
    existing_pos = None  # Disabled for multi-strategy portfolio
    
    # Add entry_regime from tracked_positions if available
    if existing_pos and symbol in tracked_positions:
        existing_pos['entry_regime'] = tracked_positions[symbol].get('entry_regime', 'unknown')
    
    # Get strategy params from SYMBOL_RULES defaults
    params = {
        'EMA_Fast': 20, 'EMA_Slow': 50,
        'ADX': 20.0,
        'ATR_Mult': 0.65,
        'RR': 2.0,
    }
    
    # Generate signals via modular portfolio orchestrator.
    df_1h, df_5m = fetch_m15_and_resample(symbol, bars=LOOKBACK_BARS)
    if df_1h is None or df_5m is None:
        print(f"[ERROR] {symbol}: Failed to fetch data")
        return (symbol, f"{symbol}:NODATA", [])
    context = {
        'df_1h': df_1h,
        'df_5m': df_5m,
        'sym_info': sym_info,
        'debug': _DEBUG_MODE,
        'risk_percent': risk,
        'open_positions_count': len(get_open_positions()),
        'symbol': symbol,  # Pass symbol for per-strategy position checking
    }
    orchestrator = _build_portfolio_orchestrator(context)
    signals = orchestrator.generate_all_signals(symbol, context)
    
    print(f"[LOG] {symbol}: Generated {len(signals)} signals from portfolio orchestrator")

    if not signals:
        return (symbol, f"{symbol}:-", [])
    
    # Add strategy_name if missing
    for signal in signals:
        if signal and 'strategy_name' not in signal:
            signal['strategy_name'] = 'portfolio_strategy'
    
    # Fixed-rule signal validation
    
    # If we have a position → MANAGEMENT state
    if existing_pos:
        set_state(BotState.MANAGEMENT)
        
        # ── SAFETY DISABLED: Max Hold Time (monte_carlo test has no safety mechanisms) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # try:
        #     open_time_str = tracked_positions.get(symbol, {}).get('open_time')
        #     if open_time_str:
        #         open_dt = datetime.strptime(open_time_str, '%Y-%m-%d %H:%M:%S')
        #         elapsed_min = (datetime.now() - open_dt).total_seconds() / 60
        #         timeout_bars = SMC_SYMBOL_RULES.get(symbol, {}).get('timeout_bars', 96)
        #         max_hold = timeout_bars * 5
        #         if elapsed_min >= max_hold:
        #             print(f"⏰ {symbol}: Max hold time exceeded ({elapsed_min:.0f} min > {max_hold} min) — force closing")
        #             if close_position(existing_pos):
        #                 return (symbol, f"{symbol}:TIME_CLOSE", None)
        # except Exception as e:
        #     print(f"[!] Max hold time check error {symbol}: {e}")
        pl = existing_pos['profit']
        
        # ── TRADE MANAGEMENT DISABLED (monte_carlo test has no trade management) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: simple entry → exit at TP/SL, no partial TP, no trailing
        # main.py: partial TP at 1R, trailing stop after partial - DISABLED
        # tracked = tracked_positions.get(symbol, {})
        # entry_price = tracked.get('entry_price') or existing_pos.get('open_price', 0)
        # original_sl = tracked.get('original_sl') or existing_pos.get('sl', 0)
        # original_tp = tracked.get('original_tp') or existing_pos.get('tp', 0)
        # current_sl = existing_pos.get('sl', original_sl)
        # digits = sym_info.get('digits', 5)
        # current_price = sym_info['bid'] if existing_pos['direction'] == 'BUY' else sym_info['ask']
        # sym_rules = SMC_SYMBOL_RULES.get(symbol, {})
        # no_partial = sym_rules.get('no_partial', False)
        # tp1_r = sym_rules.get('tp1_r', 1.0)
        # tp1_fraction = sym_rules.get('tp1_fraction', 0.5)
        # trail_mult = sym_rules.get('trail_mult', 1.5)
        # if entry_price and original_sl:
        #     initial_risk = abs(entry_price - original_sl)
        #     stop_dist = initial_risk
        #     bar_high = current_price
        #     bar_low = current_price
        #     try:
        #         broker_sym_trail = get_broker_symbol(symbol)
        #         last_bars = mt5.copy_rates_from_pos(broker_sym_trail, mt5.TIMEFRAME_M15, 0, 2)
        #         if last_bars is not None and len(last_bars) >= 2:
        #             bar_high = float(last_bars[-2]['high'])
        #             bar_low = float(last_bars[-2]['low'])
        #     except Exception:
        #         pass
        #     if existing_pos['direction'] == 'BUY':
        #         unrealized = current_price - entry_price
        #         highest_price = tracked.get('highest_price', entry_price)
        #         highest_price = max(highest_price, bar_high)
        #         lowest_price = tracked.get('lowest_price', entry_price)
        #     else:
        #         unrealized = entry_price - current_price
        #         lowest_price = tracked.get('lowest_price', entry_price)
        #         lowest_price = min(lowest_price, bar_low)
        #         highest_price = tracked.get('highest_price', entry_price)
        #     tracked_positions[symbol]['highest_price'] = highest_price
        #     tracked_positions[symbol]['lowest_price'] = lowest_price
        #     partial_taken = tracked.get('partial_taken', False)
        #     if not no_partial and not partial_taken and initial_risk > 0:
        #         tp1_dist = initial_risk * tp1_r
        #         hit_tp1 = unrealized >= tp1_dist
        #         if hit_tp1:
        #             success, closed_vol = close_partial_position(existing_pos, tp1_fraction)
        #             if success:
        #                 banked_r = tp1_r * tp1_fraction
        #                 tracked_positions[symbol]['partial_taken'] = True
        #                 tracked_positions[symbol]['banked_r'] = banked_r
        #                 tracked_positions[symbol]['remaining_fraction'] = 1.0 - tp1_fraction
        #                 new_sl = round(entry_price, digits)
        #                 if (existing_pos['direction'] == 'BUY' and new_sl > current_sl) or \
        #                    (existing_pos['direction'] == 'SELL' and new_sl < current_sl):
        #                     modify_position_sl_tp(existing_pos, new_sl=new_sl)
        #                     tracked_positions[symbol]['sl'] = new_sl
        #                 tracked_positions[symbol]['highest_price'] = bar_high
        #                 tracked_positions[symbol]['lowest_price'] = bar_low
        #                 log_event(f"{symbol}: Partial TP at 1R closed {tp1_fraction*100:.0f}% ({closed_vol} lots)", "INFO")
        #                 send_telegram_message(
        #                     f"🏦 <b>Partial TP Hit</b>\n"
        #                     f"{symbol}: Closed {tp1_fraction*100:.0f}% at 1R\n"
        #                     f"Volume: {closed_vol} lots\n"
        #                     f"Unrealized (remaining): ${unrealized:.2f}"
        #                 )
        #     else:
        #         if trail_mult is not None:
        #             trail_dist = stop_dist * trail_mult
        #             if existing_pos['direction'] == 'BUY':
        #                 new_stop = highest_price - trail_dist
        #                 if new_stop > current_sl:
        #                     new_sl = round(new_stop, digits)
        #                     if modify_position_sl_tp(existing_pos, new_sl=new_sl):
        #                         tracked_positions[symbol]['sl'] = new_sl
        #                         log_event(f"{symbol}: Trail SL → {new_sl}", "INFO")
        #     else:
        #         new_stop = lowest_price + trail_dist
        #         if new_stop < current_sl:
        #             new_sl = round(new_stop, digits)
        #             if modify_position_sl_tp(existing_pos, new_sl=new_sl):
        #                 tracked_positions[symbol]['sl'] = new_sl
        #                 log_event(f"{symbol}: Trail SL → {new_sl}", "INFO")

        # Supplementary position fields for downstream logic
        try:
            existing_pos['current_price'] = sym_info['bid'] if existing_pos['direction'] == 'BUY' else sym_info['ask']
            existing_pos['bid'] = sym_info['bid']
            existing_pos['ask'] = sym_info['ask']

        except Exception:
            pass
        
        # Backtest parity: no reversal-based discretionary closes.
        # Position lifecycle is managed by SL/TP/time-based rules only.

        # Holding position
        dir_char = '▲' if existing_pos['direction'] == 'BUY' else '▼'
        pl_str = f"+${pl:.0f}" if pl >= 0 else f"-${abs(pl):.0f}"
        return (symbol, f"{symbol}:{dir_char}{pl_str}", [])
    
    # No existing position - return all signals for execution
    return (symbol, None, signals)


def scan_markets(cfg: dict, verbose: bool = False):
    """Scan all enabled symbols for signals and execute trades (PARALLEL)."""
    global last_signals, tracked_positions
    
    # Connection health check
    if not check_mt5_connection():
        print("[!] MT5 connection lost during scan")
        if not reconnect_mt5():
            print("[!] Could not reconnect to MT5. Skipping this scan cycle.")
            update_runtime_status(state='degraded', message='MT5 reconnect failed', enabled_symbols=cfg.get('enabled_symbols', []))
            return
    
    enabled = set(cfg.get('enabled_symbols', SYMBOLS))
    risk = min(float(cfg.get('risk_percent', DEFAULT_RISK)), 1.0)

    # First, check for closed positions (SL/TP hit) - keep this sequential
    # Key by broker symbol (e.g. 'EURUSD.i') — that's what MT5 returns in pos.symbol
    current_positions = {pos.symbol: pos for pos in get_open_positions()}
    
    for symbol, prev_pos in list(tracked_positions.items()):
        broker_sym_check = get_broker_symbol(symbol)
        if broker_sym_check not in current_positions:
            # Position was closed (SL or TP hit)
            from_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
            to_date = datetime.now(timezone.utc)
            
            deals = mt5.history_deals_get(from_date, to_date)
            if deals:
                _broker_sym_match = get_broker_symbol(symbol)  # e.g. 'EURUSD.i' — MT5 stores broker name
                for deal in reversed(deals):
                    if deal.symbol == _broker_sym_match and deal.position_id == prev_pos.get('ticket'):
                        exit_price = deal.price
                        profit = deal.profit
                        
                        # Log closed trade (CSV — legacy)
                        log_trade({
                            'timestamp': prev_pos['open_time'],
                            'symbol': symbol,
                            'direction': prev_pos['direction'],
                            'entry_price': prev_pos['entry_price'],
                            'stop_loss': prev_pos.get('sl', ''),
                            'take_profit': prev_pos.get('tp', ''),
                            'lot_size': prev_pos['lot_size'],
                            'risk_percent': prev_pos.get('risk_percent', risk),
                            'status': 'CLOSED',
                            'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'exit_price': exit_price,
                            'profit': f"{profit:.2f}"
                        })
                        
                        # Log closed trade (SQLite database)
                        try:
                            close_trade(
                                prev_pos.get('ticket', 0),
                                close_price=exit_price,
                                profit=profit,
                                exit_reason='SL/TP',
                            )
                            log_event(f"Trade closed: {prev_pos['direction']} {symbol} profit=${profit:.2f}", "INFO")
                            _push_logs_async([f"Trade Closed: {prev_pos['direction']} {symbol} profit=${profit:.2f}"])
                            # Push trade close to remote dashboard
                            _push_trade_async({
                                'action': 'close',
                                'ticket': prev_pos.get('ticket', 0),
                                'close_price': exit_price,
                                'profit': profit,
                                'exit_reason': 'SL/TP',
                                'close_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            })
                        except Exception:
                            pass
                        
                        result_emoji = "✅" if profit > 0 else "❌"
                        send_telegram_message(
                            f"{result_emoji} <b>Trade Closed</b>\n"
                            f"{prev_pos['direction']} {symbol}\n"
                            f"Entry: {prev_pos['entry_price']}\n"
                            f"Exit: {exit_price}\n"
                            f"Profit: ${profit:.2f}"
                        )
                        
                        # ── Safety state updates (matching backtest engine) ──
                        today_str = datetime.now().strftime('%Y-%m-%d')
                        
                        # Determine exit reason from deal
                        exit_reason = 'unknown'
                        if deal.reason == 3:  # DEAL_REASON_SL
                            exit_reason = 'SL'
                        elif deal.reason == 4:  # DEAL_REASON_TP
                            exit_reason = 'TP'
                        elif profit < 0:
                            exit_reason = 'SL'  # Assume SL if lost money
                        else:
                            exit_reason = 'TP'
                        
                        # ── SAFETY DISABLED: Consecutive loss tracking (monte_carlo test has no safety mechanisms) ──
                        # Disabled for true 1:1 parity with monte_carlo_robustness.py
                        # if profit < 0:
                        #     consecutive_losses[symbol] = consecutive_losses.get(symbol, 0) + 1
                        #     if consecutive_losses[symbol] >= 2:
                        #         sl_cooldown_until[symbol] = datetime.now(timezone.utc) + timedelta(minutes=SL_COOLDOWN_MINUTES)
                        #         blocked_symbols[symbol] = today_str
                        #         print(f"🚫 {symbol}: 2 consecutive losses — 60min cooldown + blocked for today")
                        #         log_event(f"{symbol}: Blocked after 2 consecutive losses", "WARN")
                        # else:
                        #     consecutive_losses[symbol] = 0

                        # Virtual balance tracking DISABLED for 1:1 parity
                        
                        break
            
            del tracked_positions[symbol]
    
    # PARALLEL SCAN: Process all symbols concurrently
    status = []
    signals_to_execute = []
    
    with ThreadPoolExecutor(max_workers=len(SYMBOLS)) as executor:
        # Submit all symbol processing tasks
        future_to_symbol = {executor.submit(process_single_symbol, symbol, enabled, risk): symbol 
                           for symbol in SYMBOLS}
        
        # Collect results as they complete
        for future in as_completed(future_to_symbol):
            symbol, status_str, signals = future.result()
            
            if status_str:
                status.append(status_str)
            
            if signals:
                # Add all signals from this symbol to the execution list
                for signal in signals:
                    signals_to_execute.append((symbol, signal))
    
    print(f"[LOG] Total signals to execute: {len(signals_to_execute)}")
    
    # Execute any new signals (sequential for safety)
    for idx, (symbol, signal) in enumerate(signals_to_execute):
        print(f"[LOG] Executing signal {idx+1}/{len(signals_to_execute)}: {symbol} {signal.get('direction')}")
        set_state(BotState.EXECUTION)
        
        today_str = datetime.now().strftime('%Y-%m-%d')

        # ── SAFETY DISABLED: Daily blocks, consecutive losses, kill switch, daily target, daily DD (monte_carlo test has no safety mechanisms) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # # Reset daily blocks
        # for sym in list(blocked_symbols.keys()):
        #     if blocked_symbols[sym] != today_str:
        #         del blocked_symbols[sym]
        #         consecutive_losses[sym] = 0
        # # Consecutive loss blocker
        # if symbol in blocked_symbols and blocked_symbols[symbol] == today_str:
        #     print(f"🚫 {symbol}: Blocked after 2 consecutive losses — skipping")
        #     status.append(f"{symbol}:BLOCKED_LOSSES")
        #     continue
        # # Cooldown check
        # if symbol in sl_cooldown_until:
        #     if datetime.now(timezone.utc) < sl_cooldown_until[symbol]:
        #         remaining = (sl_cooldown_until[symbol] - datetime.now(timezone.utc)).total_seconds() / 60
        #         print(f"⏳ {symbol}: Consecutive-loss cooldown — {remaining:.0f} min remaining")
        #         status.append(f"{symbol}:COOLDOWN")
        #         continue
        #     else:
        #         del sl_cooldown_until[symbol]

        # All safety checks DISABLED for 1:1 parity with monte_carlo tests

        # NEW SIGNAL - this is important, print it
        sym_info = get_symbol_info(symbol)
        if not is_symbol_tradeable(sym_info):
            print(f"[~] {symbol}: market closed / not tradeable — skipping order attempt")
            status.append(f"{symbol}:MARKET_CLOSED")
            continue

        # ── Stop distance spread check DISABLED (monte_carlo test has no spread check) ──
        # Disabled for true 1:1 parity with monte_carlo_robustness.py
        # monte_carlo test: no spread check on stop distance
        # main.py: stop distance spread check - DISABLED
        # # Backtest line 1816: skip if stop_dist <= spread * 2 (too tight — spread eats the trade)
        # _stop_dist_check = abs(signal['entry'] - signal['stop'])
        # _spread_price = sym_info.get('spread', 0) * sym_info.get('tick_size', 0.00001) if sym_info else 0
        # if _spread_price > 0 and _stop_dist_check <= _spread_price * 2:
        #     print(f"⚠️ {symbol}: Stop too tight (dist={_stop_dist_check:.5f} <= spread×2={_spread_price*2:.5f}) — skipping")
        #     status.append(f"{symbol}:STOP_TOO_TIGHT")
        #     continue

        dir_char = '▲' if signal['direction'] == 'BUY' else '▼'
        sig_line = f">>> NEW SIGNAL: {dir_char} {signal['direction']} {symbol} @ {signal['entry']} | TP:{signal['tp']} SL:{signal['stop']}"
        print(f"\n{sig_line}")
        _push_logs_async([sig_line])
        
        # Backtest parity: dd_factor + vol_factor on lot size (mirrors backtest_improved.py exactly)
        htf_bias = 'N/A'
        dd_factor_live = 1.0
        vol_factor_live = 1.0
        # DD factor DISABLED for 1:1 parity with monte_carlo tests
        # vol_factor: ATR ratio scaling — matches backtest lines 1827-1837 exactly
        # Backtest uses _atr(df_m5) — resampled M5 data, NOT raw M15.
        # ratio>1.6 → scale risk to 0.6x (high vol), ratio<0.7 → scale to 0.8x (low vol)
        try:
            from OLDBOT.mt5_bot.smart_money_strategy import _atr as _smc_atr
            _df_m15_vf = fetch_live_candles(symbol, timeframe=mt5.TIMEFRAME_M15, bars=100)
            if _df_m15_vf is not None and len(_df_m15_vf) >= 20:
                if not isinstance(_df_m15_vf.index, pd.DatetimeIndex):
                    _df_m15_vf = _df_m15_vf.set_index(pd.to_datetime(_df_m15_vf['Time']))
                # Resample to M5 — matches backtest df_m5 ATR computation
                _df_m5_vf = _resample_m15_to_tf(_df_m15_vf, '5min').iloc[:-1]
                atr_s = _smc_atr(_df_m5_vf)
                atr_window = atr_s.iloc[-80:].dropna()
                if len(atr_window) >= 20:
                    atr_now_vf = float(atr_window.iloc[-1])
                    atr_med_vf = float(atr_window.median())
                    if atr_med_vf > 0:
                        ratio_vf = atr_now_vf / atr_med_vf
                        if ratio_vf > 1.6:
                            vol_factor_live = 0.6
                        elif ratio_vf < 0.7:
                            vol_factor_live = 0.8
        except Exception:
            pass
        risk_used = float(risk) * dd_factor_live * vol_factor_live

        # Don't send signal alert - will send when position actually opens
        print(f"[LOG] Attempting to open position for {symbol}...")
        success = open_position_with_retry(signal, sym_info, risk_used)
        
        if success:
            print(f"[LOG] Position opened successfully for {symbol}")
            sig_key = f"{symbol}_{signal['direction']}"
            last_signals[sig_key] = datetime.now(timezone.utc)
            status.append(f"{symbol}:OPENED")
            
            send_telegram_message(
                f"✅ <b>Position Opened</b>\n"
                f"{signal['direction']} {symbol}\n"
                f"Entry: {signal['entry']}\n"
                f"🎯 TP: {signal['tp']}\n"
                f"🛑 SL: {signal['stop']}\n"
                f"⚠️ Risk: {risk_used}%  | HTF: {htf_bias}"
            )
            
            # Track this position for closure detection
            pos = get_position_for_symbol(symbol)
            if pos:
                # Get current regime for entry tracking
                entry_regime = 'unknown'
                
                tracked_positions[symbol] = {
                    'ticket': pos.get('ticket'),
                    'strategy_name': signal.get('strategy_name', 'smart_money_v1'),
                    'direction': signal['direction'],
                    'entry_price': signal['entry'],
                    'original_sl': signal['stop'],
                    'original_tp': signal['tp'],
                    'sl': signal['stop'],
                    'tp': signal['tp'],
                    'lot_size': signal.get('lot_size', 0),
                    'open_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'entry_regime': entry_regime,
                    'risk_percent': risk_used,
                    'htf_bias': htf_bias,
                    # Backtest-matching trade management fields
                    'highest_price': signal['entry'],  # For trailing (tracks highest price for buys)
                    'lowest_price': signal['entry'],   # For trailing (tracks lowest price for sells)
                    'partial_taken': False,            # Whether partial TP was taken
                    'banked_r': 0.0,                  # R-multiple banked from partial close
                    'remaining_fraction': 1.0,        # Remaining position fraction (1.0 = 100%)
                }
                
                # ── SQLite: record trade open ──
                try:
                    insert_trade(
                        ticket=pos.get('ticket', 0),
                        symbol=symbol,
                        strategy_name=signal.get('strategy_name', 'smart_money_v1'),
                        trade_type=signal['direction'],
                        open_price=signal['entry'],
                        sl=signal['stop'],
                        tp=signal['tp'],
                        lot_size=signal.get('lot_size', 0),
                        risk_percent=risk_used,
                    )
                    log_event(f"Trade opened: {signal['direction']} {symbol} @ {signal['entry']}", "INFO")
                    # Push trade open to remote dashboard
                    _push_trade_async({
                        'action': 'open',
                        'ticket': pos.get('ticket', 0),
                        'symbol': symbol,
                        'strategy_name': signal.get('strategy_name', 'smart_money_v1'),
                        'trade_type': signal['direction'],
                        'open_price': signal['entry'],
                        'sl': signal['stop'],
                        'tp': signal['tp'],
                        'lot_size': signal.get('lot_size', 0),
                        'risk_percent': risk_used,
                        'open_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    })
                except Exception:
                    pass
        else:
            status.append(f"{symbol}:FAIL")
            # Don't spam Telegram on failed orders
    
    # Single compact status line (sorted by symbol order in SYMBOLS)
    status_dict = {s.split(':')[0]: s for s in status}
    sorted_status = [status_dict.get(sym, f"{sym}:-") for sym in SYMBOLS if sym in enabled]
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"\r[{ts}] {' | '.join(sorted_status)}   ", end='', flush=True)

    opened_count = sum(1 for s in status if s.endswith(':OPENED'))
    fail_count = sum(1 for s in status if s.endswith(':FAIL'))

    # Grab account info for dashboard
    acct_balance = 0.0
    acct_equity = 0.0
    acct_profit = 0.0
    try:
        acct = mt5.account_info()
        if acct:
            acct_balance = float(acct.balance)
            acct_equity = float(acct.equity)
            acct_profit = float(acct.profit)
    except Exception:
        pass

    # Serialize full position details so the dashboard can display them
    # without needing its own MT5 connection
    position_details = []
    try:
        raw_positions = get_open_positions()
        for pos in raw_positions:
            position_details.append({
                'ticket': int(pos.ticket),
                'symbol': pos.symbol,
                'direction': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
                'volume': float(pos.volume),
                'open_price': float(pos.price_open),
                'current_price': float(pos.price_current),
                'sl': float(pos.sl) if pos.sl else 0.0,
                'tp': float(pos.tp) if pos.tp else 0.0,
                'profit': float(pos.profit),
                'swap': float(pos.swap),
                'open_time': datetime.fromtimestamp(pos.time, tz=timezone.utc).isoformat(),
            })
    except Exception:
        pass

    update_runtime_status(
        state='running',
        message='Scan cycle completed',
        enabled_symbols=sorted(enabled),
        open_positions=len(position_details),
        position_details=position_details,
        signals_detected=len(signals_to_execute),
        positions_opened=opened_count,
        failed_orders=fail_count,
        status_line=' | '.join(sorted_status),
        balance=acct_balance,
        equity=acct_equity,
        floating_pnl=acct_profit,
    )

    # Push scan log line to remote dashboard for activity feed
    scan_log_line = f"[{ts}] {' | '.join(sorted_status)}"
    _push_logs_async([scan_log_line])


def main():
    parser = argparse.ArgumentParser(description="LIVE Trading Bot")
    parser.add_argument('--once', action='store_true', help='Single scan and exit')
    parser.add_argument('--loop', type=int, default=900, help='Seconds between scans (default: 900 = M15 candle close)')
    parser.add_argument('--debug', action='store_true', help='Print per-filter rejection reason for each symbol')
    args = parser.parse_args()
    global _DEBUG_MODE
    _DEBUG_MODE = args.debug
    if _DEBUG_MODE:
        print("[DEBUG MODE ON] Will print filter rejection reasons for each symbol")

    print("=" * 60)
    print("  ZENITH TRADING BOT — ICT / SMC Engine")
    print("=" * 60)

    # Initialize SQLite database (trades, order_blocks, logs)
    init_trading_db()
    log_event("Bot process starting", "INFO")
    
    # Initialize hedge fund data collector
    init_data_collector()
    data_collector = get_data_collector()
    log_event("Hedge fund data collector initialized", "INFO")

    # Initialize MT5
    if not init_mt5():
        print("[!] Cannot start without MT5 connection")
        update_runtime_status(state='error', message='Cannot start without MT5 connection')
        return
    
    print(f"[✓] MT5 connected: {mt5.terminal_info().name}")
    print(f"[✓] Account: {mt5.account_info().login}")
    
    cfg = load_config()
    print(f"[✓] Risk: {cfg['risk_percent']}%")
    print(f"[✓] Symbols: {', '.join(cfg['enabled_symbols'])}")
    
    # Telegram bot for commands
    tg = TelegramBot()
    if tg.is_configured():
        print("[✓] Telegram connected")
    
    # Show existing open positions
    show_open_positions()
    
    print("[+] Scanning every M15 candle close (Ctrl+C to stop)\n")

    try:
        if args.once:
            cfg = load_config()
            scan_markets(cfg)
        else:
            # Start Telegram polling in separate thread for instant response
            telegram_active = threading.Event()
            telegram_active.set()
            
            def telegram_loop():
                nonlocal cfg
                while telegram_active.is_set():
                    cfg = tg.poll_commands(cfg)
                    time.sleep(1)
            
            telegram_thread = threading.Thread(target=telegram_loop, daemon=True)
            telegram_thread.start()
            

            # Market scanning loop — synchronized to M15 candle close
            CANDLE_SECONDS = 900  # M15 = 15 * 60
            SCAN_OFFSET    = 0    # scan immediately at candle close for 1:1 parity with backtest

            def _wait_for_m15_close():
                """Sleep until the next M15 candle boundary + SCAN_OFFSET seconds."""
                now = time.time()
                elapsed_in_candle = now % CANDLE_SECONDS
                wait = (CANDLE_SECONDS - elapsed_in_candle) + SCAN_OFFSET
                next_close = datetime.utcfromtimestamp(now + wait).strftime('%H:%M:%S')
                print(f"\r[~] Next scan at {next_close} UTC ({wait:.0f}s)   ", end='', flush=True)
                time.sleep(wait)

            while True:
                _wait_for_m15_close()
                
                try:
                    cfg = load_config()
                    scan_markets(cfg)
                except Exception as e:
                    print(f"\n[!] Scan loop error: {e}")

    except KeyboardInterrupt:
        print("\n[!] Stopped by user")
        update_runtime_status(state='stopped', message='Stopped by user')
        if not args.once:
            telegram_active.clear()
        sys.exit(0)  # Exit cleanly with code 0
    finally:
        # Save final data snapshot before shutdown
        try:
            data_collector = get_data_collector()
            snapshot_file = data_collector.save_json_snapshot()
            print(f"[✓] Final data snapshot saved: {snapshot_file}")
        except Exception as e:
            print(f"[!] Failed to save final snapshot: {e}")
        
        shutdown_mt5()
        update_runtime_status(state='stopped', message='MT5 disconnected')
        print("[✓] MT5 disconnected")


if __name__ == '__main__':
    main()
