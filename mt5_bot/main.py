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

# .env support (MT5_LOGIN, TELEGRAM_BOT_TOKEN, RISK_PER_TRADE, …)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass  # python-dotenv optional; falls back to json configs

from strategy import get_instrument_settings
from smart_money_strategy import SmartMoneyStrategy, should_trade
from telegram_bot import send_telegram_message, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# SQLite database (trades, order_blocks, logs)
from db import (
    init_trading_db, insert_trade, close_trade, get_daily_pnl,
    insert_order_block, mitigate_order_block, get_active_order_blocks,
    log_event, get_trades,
)


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

# Pure ICT/SMC strategy - no AI layers
# STRATEGY SELECTOR: True = Smart Money (Liquidity Sweep + MSS), False = Original ICT/SMC
USE_SMART_MONEY_STRATEGY = True

# config

# Symbols to trade - FINAL FORM: TOP 3 ONLY (ChatGPT Approved)
# CORE: EURUSD, NAS100, XAUUSD (Gold)
# ⚠️ NO SECONDARY/ADVANCED - prevents overtrading & correlated positions
SYMBOLS = ['EURUSD', 'NAS100', 'XAUUSD']

# Broker uses '.i' suffix for most symbols
BROKER_SUFFIX = {'EURUSD': '.i', 'GBPUSD': '.i', 'USDJPY': '.i', 'XAUUSD': '.i', 'EURJPY': '.i'}

# Strategy constants (same as backtested)
# Session kill zones: London 07-11 UTC, New York 13-17 UTC
SESSION_LONDON_START = 7
SESSION_LONDON_END   = 11
SESSION_NY_START     = 13
SESSION_NY_END       = 17
ATR_PERIOD = 14
ADX_PERIOD = 14
ADX_THRESHOLD = 25       # Doc: ADX < 25 = consolidation → skip
LOOKBACK_BARS = 3000     # closer to 30-day M15 backtest horizon (~2880 bars)
TP_RR_RATIO = 2.5        # Take Profit at 2.5:1 risk-reward (aligned with improved backtest engine)

# ICT / SMC constants (ported from validated backtest engine)
BOS_VALIDITY = 50        # Order Block zone valid for 50 bars
OB_SCAN = 20             # Look back up to 20 bars for OB candle
SWING_LOOKBACK = 5       # Swing-point detection window
MIN_CONFLUENCE = 0.8     # Match backtest aggressive confluence (was 4 - too restrictive)
COOLDOWN_BARS = 0        # NO SIGNAL SPACING - back-to-back entries for maximum profit (matches backtest)
FULL_TIME_TRADING = True # Match backtest default: no session-hour restriction when True

# Safety mechanisms — circuit breakers
MAX_DAILY_DRAWDOWN = float(os.getenv('MAX_DAILY_DRAWDOWN', '3.0'))   # 3% daily max loss → deactivate (matches backtest)
MAX_MARGIN_USAGE   = float(os.getenv('MAX_MARGIN_USAGE', '95.0'))    # Allow high margin usage (user controls risk)

# Runtime config persistence
CONFIG_FILE = Path(__file__).parent / 'runtime_config.json'
DEFAULT_RISK = float(os.getenv('RISK_PER_TRADE', '2.0'))  # Doc: 2 % per trade
MIN_RUNTIME_RISK = float(os.getenv('MIN_RUNTIME_RISK', '0.10'))
# Allow higher runtime risk (user-requested). Default max set to 20% per trade.
MAX_RUNTIME_RISK = float(os.getenv('MAX_RUNTIME_RISK', '20.00'))

# Track last signal to avoid spam
last_signals = {}

# Per-symbol confluence gates (EXACT MATCH TO BACKTEST)
def get_min_confluence(symbol):
    """OPTIMIZED confluence gates - instrument-specific tuning"""
    table = {
        'BTCUSD': 0.8,   # More conservative - crypto is volatile
        'EURUSD': 0.7,   # Already good
        'GBPUSD': 0.7,   # Already good
        'GBPJPY': 0.8,   # Already good
        'XAUUSD': 0.5,   # Aggressive - gold responds well
        'USDJPY': 0.8,   # Already good
        'NAS100': 0.7,   # More conservative - index volatility
    }
    return table.get(symbol, 0.8)


def get_adx_floor(symbol):
    """Per-instrument ADX minimums (match backtest _get_adx_floor)."""
    table = {
        'USDJPY': 12.0,
        'GBPJPY': 12.0,
        'XAUUSD': 13.0,
        'EURUSD': 13.0,
        'GBPUSD': 14.0,
        'BTCUSD': 14.0,
        'NAS100': 13.0,
    }
    return table.get(symbol, 12.0)

# Track open positions to detect closures
tracked_positions = {}

# ── Safety state (matching backtest engine) ──────────────────────────────
# Daily trade cap: 6/day forex, 8/day crypto (BTCUSD) — matches backtest
daily_trade_count = {}   # {symbol: {date_str: int}}

# Consecutive loss blocker: 3 consecutive SLs → block symbol for the day
consecutive_losses = {}  # {symbol: int}
blocked_symbols = {}     # {symbol: date_str} — blocked until next day

# Post-SL cooldown: 4 bars (~1 hour on M15) after any SL
sl_cooldown_until = {}   # {symbol: datetime}
SL_COOLDOWN_MINUTES = 60  # 4 bars × 15 min = 60 min

# Backtest-parity virtual balance state (per symbol)
# Mirrors backtest's per-symbol balance/day-balance logic for DD blocking.
BACKTEST_INITIAL_BALANCE = 10000.0
BACKTEST_DD_LIMIT_PCT = 3.0
symbol_virtual_balance = {}    # {symbol: float}
symbol_day_start_balance = {}  # {symbol: float}
symbol_day_marker = {}         # {symbol: 'YYYY-MM-DD'}

# Max hold time: auto-close positions exceeding max bars
MAX_HOLD_MINUTES_FOREX = 96 * 15   # 96 bars × 15 min = 1440 min = 24 h
MAX_HOLD_MINUTES_CRYPTO = 120 * 15 # 120 bars × 15 min = 1800 min = 30 h

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
                return True
    except Exception as e:
        print(f"[!] Reconnection failed: {e}")
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
    }


# position mgmt

def get_open_positions() -> list:
    """Get all open positions from MT5."""
    positions = mt5.positions_get()
    if positions is None:
        return []
    return list(positions)


def get_position_for_symbol(symbol: str) -> dict | None:
    """Check if we have an open position for this symbol."""
    broker_sym = get_broker_symbol(symbol)
    positions = mt5.positions_get(symbol=broker_sym)
    if positions is None or len(positions) == 0:
        return None
    
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
    }


def calculate_lot_size(symbol: str, risk_percent: float, stop_distance: float, sym_info: dict) -> float:
    """Calculate lot size based on risk percentage and stop distance."""
    account = mt5.account_info()
    if account is None:
        return sym_info['volume_min']
    
    balance = account.balance
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
    broker_sym = signal['broker_symbol']
    
    # Determine order type
    if signal['direction'] == 'BUY':
        order_type = mt5.ORDER_TYPE_BUY
        price = sym_info['ask']  # fresh ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = sym_info['bid']  # fresh bid
    
    request = {
        'action': mt5.TRADE_ACTION_DEAL,
        'symbol': broker_sym,
        'volume': lot_size,
        'type': order_type,
        'price': price,
        'sl': signal['stop'],
        'tp': signal['tp'],
        'deviation': 20,  # slippage in points
        'magic': 123456,  # EA magic number
        'comment': 'TrendBot',
        'type_time': mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }
    
    result = mt5.order_send(request)
    return result


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
    
    return False


def close_position(position: dict) -> bool:
    """Close an existing position."""
    broker_sym = position['broker_symbol']
    ticket = position['ticket']
    volume = position['volume']
    
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
        'volume': volume,
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
        print(f"[✓] Position closed: {position['direction']} {volume} {position['symbol']}")
        return True
    
    print(f"[!] Close failed: {result.retcode if result else mt5.last_error()}")
    return False


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
    # Check connection first
    if not check_mt5_connection():
        if not reconnect_mt5():
            return None
    
    broker_sym = get_broker_symbol(symbol)
    
    # Ensure symbol is visible in Market Watch
    if not mt5.symbol_select(broker_sym, True):
        # Try once more after small delay
        time.sleep(0.5)
        if not mt5.symbol_select(broker_sym, True):
            last_error = mt5.last_error()
            print(f"[!] Failed to select {broker_sym}: {last_error}")
            return None
    
    rates = mt5.copy_rates_from_pos(broker_sym, timeframe, 0, bars)
    if rates is None or len(rates) < 100:
        return None
    
    df = pd.DataFrame(rates)
    df['Time'] = pd.to_datetime(df['time'], unit='s')
    df = df[['Time', 'open', 'high', 'low', 'close', 'tick_volume']]
    df.columns = ['Time', 'Open', 'High', 'Low', 'Close', 'Volume']
    return df


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


def _find_swing_points(highs, lows, lookback=SWING_LOOKBACK):
    """Detect swing highs and swing lows using lookback window (no future leak)."""
    n = len(highs)
    swing_highs = np.full(n, np.nan)
    swing_lows = np.full(n, np.nan)
    for i in range(lookback, n - lookback):
        wh = highs[i - lookback: i + lookback + 1]
        wl = lows[i - lookback: i + lookback + 1]
        if highs[i] == np.max(wh):
            swing_highs[i] = highs[i]
        if lows[i] == np.min(wl):
            swing_lows[i] = lows[i]
    return swing_highs, swing_lows


def in_session_kill_zone(hour: int) -> bool:
    """Return True if current hour falls inside London or NY kill zone."""
    return (SESSION_LONDON_START <= hour <= SESSION_LONDON_END) or \
           (SESSION_NY_START <= hour <= SESSION_NY_END)


def _is_rejection_candle_live(o, h, l, c, direction, strict=False):
    """
    Check for bullish (direction=1) or bearish (direction=-1) rejection.
    STRICTER version: tighter thresholds to avoid false signals.
    strict=True raises thresholds to filter weaker rejections.
    EXACT MATCH TO BACKTEST
    """
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)

    if strict:
        # Beast-mode: tighter thresholds
        if direction == 1:  # bullish
            lower_wick = min(o, c) - l
            # Strong bullish close
            if c > o and (c - l) / rng >= 0.68:
                return True
            # Very strong hammer
            if lower_wick / rng >= 0.50:
                return True
            # Strong bullish body
            if body / rng >= 0.70:
                return True
        else:  # bearish
            upper_wick = h - max(o, c)
            # Strong bearish close
            if c < o and (h - c) / rng >= 0.68:
                return True
            # Very strong inverted hammer
            if upper_wick / rng >= 0.50:
                return True
            # Strong bearish body
            if body / rng >= 0.70:
                return True
    else:
        # Standard thresholds (original)
        if direction == 1:  # bullish
            lower_wick = min(o, c) - l
            if c > o and (c - l) / rng >= 0.55:
                return True
            if lower_wick / rng >= 0.40:
                return True
            if c > o and body / rng >= 0.60:
                return True
        else:  # bearish
            upper_wick = h - max(o, c)
            if c < o and (h - c) / rng >= 0.55:
                return True
            if upper_wick / rng >= 0.40:
                return True
            if c < o and body / rng >= 0.60:
                return True
    return False


def generate_signal(df: pd.DataFrame, params: dict, symbol: str, sym_info: dict) -> dict | None:
    """Generate ICT / SMC trading signal — SYNCED with backtest engine.

    Entry types (identical to backtest_improved.py):
      1. Order Block retest  (demand/supply after BOS, rejection candle)
      2. Fair Value Gap fill (imbalance zone re-entry, directional close)
      3. Liquidity sweep     (stop-hunt reversal, rejection + reclaim)

    Filters:
      • Market structure (struct >= 0 for buy, struct <= 0 for sell)
      • ADX threshold (from best_settings.json)
      • Session killzones (London 07-11, NY 13-17, US 14-20, crypto 24/7)
      • Confluence gate >= 1 (EMA trend, zone, RSI non-extreme, strong ADX)
    """
    set_state(BotState.SCANNING)

    if not params:
        return None

    fast = int(params.get('EMA_Fast', 9))
    slow = int(params.get('EMA_Slow', 21))
    adx_th = float(params.get('ADX', ADX_THRESHOLD))
    atr_mult = float(params.get('ATR_Mult', 1.5))
    rr_ratio = float(params.get('RR', TP_RR_RATIO))
    rr_ratio = max(0.5, min(rr_ratio, 6.0))

    # Fixed parameters only (no adaptive layer)

    df = add_indicators(df, fast, slow).fillna(0)
    n = len(df)
    if n < max(slow, 60):
        return None

    C  = df['Close'].to_numpy().astype(float)
    O  = df['Open'].to_numpy().astype(float)
    H  = df['High'].to_numpy().astype(float)
    L  = df['Low'].to_numpy().astype(float)
    ema_f_arr = df['EMA_Fast'].to_numpy().astype(float)
    ema_s_arr = df['EMA_Slow'].to_numpy().astype(float)
    adx_arr   = df['ADX'].to_numpy().astype(float)
    atr_arr   = df['ATR'].to_numpy().astype(float)
    rsi_arr   = df['RSI'].to_numpy().astype(float)
    times     = pd.to_datetime(df['Time'])
    hours     = times.dt.hour.to_numpy()

    # ── Swing detection (matching backtest: left=5, right=3) ──
    SL_LEFT = 5; SR_RIGHT = 3
    sh_list = []   # (bar, price) confirmed swing highs
    sl_list = []   # (bar, price) confirmed swing lows
    for j in range(SL_LEFT, n - SR_RIGHT):
        is_sh = True
        for k in range(1, SL_LEFT + 1):
            if H[j - k] > H[j]: is_sh = False; break
        if is_sh:
            for k in range(1, SR_RIGHT + 1):
                if H[j + k] >= H[j]: is_sh = False; break
        if is_sh:
            sh_list.append((j, float(H[j])))

        is_sl = True
        for k in range(1, SL_LEFT + 1):
            if L[j - k] < L[j]: is_sl = False; break
        if is_sl:
            for k in range(1, SR_RIGHT + 1):
                if L[j + k] <= L[j]: is_sl = False; break
        if is_sl:
            sl_list.append((j, float(L[j])))

    # Keep recent swings
    sh_list = sh_list[-25:]
    sl_list = sl_list[-25:]

    # ── Market structure (same as backtest) ──
    struct = 0
    if len(sh_list) >= 2 and len(sl_list) >= 2:
        hh = sh_list[-1][1] > sh_list[-2][1]
        hl = sl_list[-1][1] > sl_list[-2][1]
        lh = sh_list[-1][1] < sh_list[-2][1]
        ll = sl_list[-1][1] < sl_list[-2][1]
        if hh and hl: struct = 1
        elif lh and ll: struct = -1

    set_state(BotState.ZONING)

    # ── Detect BOS → create OB zones (scan recent ~120 bars) ──
    OB_LOOK = 15; MAX_OB = 80; MAX_FVG = 50
    start_idx = max(slow, SL_LEFT + SR_RIGHT + 20, 60)
    # Match backtest behavior more closely: build zones across full available window.
    scan_from = start_idx

    obs = []   # {d: 1/-1, lo, hi, b, bb, ok}
    fvgs = []  # {d: 1/-1, lo, hi, b}

    for i in range(scan_from, n):
        p = i - 1
        if p < start_idx:
            continue

        # Detect bullish BOS (close above last swing high)
        if sh_list:
            lsh = sh_list[-1][1]
            if C[p] > lsh and (p < 2 or C[p-1] <= lsh):
                for j in range(p-1, max(p - OB_LOOK, start_idx), -1):
                    if C[j] < O[j] and (H[j] - L[j]) > 0:
                        obs.append({'d': 1, 'lo': float(L[j]), 'hi': float(H[j]),
                                    'b': j, 'bb': p, 'ok': True})
                        try:
                            insert_order_block(symbol=symbol, price_high=float(H[j]),
                                               price_low=float(L[j]), direction='BULL')
                        except Exception:
                            pass
                        break

        # Detect bearish BOS (close below last swing low)
        if sl_list:
            lsl = sl_list[-1][1]
            if C[p] < lsl and (p < 2 or C[p-1] >= lsl):
                for j in range(p-1, max(p - OB_LOOK, start_idx), -1):
                    if C[j] > O[j] and (H[j] - L[j]) > 0:
                        obs.append({'d': -1, 'lo': float(L[j]), 'hi': float(H[j]),
                                    'b': j, 'bb': p, 'ok': True})
                        try:
                            insert_order_block(symbol=symbol, price_high=float(H[j]),
                                               price_low=float(L[j]), direction='BEAR')
                        except Exception:
                            pass
                        break

        # Detect FVGs (matching backtest: gap > ATR * 0.20, directional)
        if p >= 2:
            ap = max(atr_arr[p], 1e-10)
            g_b = L[p] - H[p-2]
            if g_b > ap * 0.20 and C[p] > C[p-2]:
                fvgs.append({'d': 1, 'lo': float(H[p-2]), 'hi': float(L[p]), 'b': p})
            g_s = L[p-2] - H[p]
            if g_s > ap * 0.20 and C[p] < C[p-2]:
                fvgs.append({'d': -1, 'lo': float(H[p]), 'hi': float(L[p-2]), 'b': p})

    # Expire old zones
    latest = n - 1
    obs  = [o for o in obs  if (latest - o['bb']) < MAX_OB and o['ok']]
    fvgs = [f for f in fvgs if (latest - f['b']) < MAX_FVG]
    for o in obs:
        if o['d'] == 1  and C[latest-1] < o['lo'] - atr_arr[latest-1]*0.5: o['ok'] = False
        if o['d'] == -1 and C[latest-1] > o['hi'] + atr_arr[latest-1]*0.5: o['ok'] = False

    set_state(BotState.MONITORING)

    # ── Check entry on the LATEST confirmed bar (p = n-2) ──
    p = n - 2   # signal bar (confirmed)
    i = n - 1   # execution bar
    if p < start_idx:
        return None

    bar_time = df['Time'].iat[i]
    hour = hours[p]
    adx_val = adx_arr[p]
    atr_val = atr_arr[p]
    rsi_val = rsi_arr[p]

    # Use LIVE bid/ask from MT5
    bid = sym_info['bid']
    ask = sym_info['ask']
    spread_points = sym_info['spread']
    digits = sym_info['digits']

    # ── Session filter (matching backtest FULL_TIME_TRADING behavior) ──
    is_crypto = symbol in ('BTCUSD',)
    is_us = symbol in ('NAS100',)
    if not FULL_TIME_TRADING:
        if is_crypto:
            pass  # 24/7
        elif is_us:
            if hour < 14 or hour > 20:
                set_state(BotState.IDLE)
                return None
        else:
            if not ((7 <= hour <= 11) or (13 <= hour <= 17)):
                set_state(BotState.IDLE)
                return None

    # ADX filter (match backtest: max(adx_from_params, per-instrument floor))
    adx_floor = max(adx_th, get_adx_floor(symbol))
    if not np.isfinite(adx_val) or adx_val < adx_floor:
        return None

    if atr_val <= 0:
        return None

    # Backtest does not use a spread/ATR entry gate; keep behavior consistent.
    tick_size = max(float(sym_info.get('tick_size', 0.0)), 0.0)

    # ── Trend & Zone filters (matching backtest) ──
    ema_f_p = ema_f_arr[p]
    ema_s_p = ema_s_arr[p]
    ema_bull = C[p] > ema_s_p and ema_f_p > ema_s_p
    ema_bear = C[p] < ema_s_p and ema_f_p < ema_s_p

    RANGE_BARS = 50
    range_hi = float(np.max(H[max(0, p - RANGE_BARS):p + 1]))
    range_lo = float(np.min(L[max(0, p - RANGE_BARS):p + 1]))
    range_mid = (range_hi + range_lo) / 2.0
    in_discount = C[p] < range_mid
    in_premium  = C[p] > range_mid

    # ── 3 ENTRY TYPES (WEIGHTED - EXACT BACKTEST MATCH) ──
    sig = 0
    entry_type = ''
    sig_weight = 0.0  # Signal weighting system (matches backtest)

    # Determine if we need strict rejection (low confidence confluence only) - EXACT BACKTEST MATCH
    force_strict_rej = False

    # 1. Order Block retest (structure + rejection candle) - MOST RELIABLE (weight=1.5)
    for ob in obs:
        if not ob['ok']:
            continue
        if ob['d'] == 1 and struct >= 0:
            if L[p] <= ob['hi'] and C[p] >= ob['lo']:
                if _is_rejection_candle_live(O[p], H[p], L[p], C[p], 1, strict=force_strict_rej):
                    sig = 1; sig_weight = 1.5; ob['ok'] = False; entry_type = 'OB'; break
        elif ob['d'] == -1 and struct <= 0:
            if H[p] >= ob['lo'] and C[p] <= ob['hi']:
                if _is_rejection_candle_live(O[p], H[p], L[p], C[p], -1, strict=force_strict_rej):
                    sig = -1; sig_weight = 1.5; ob['ok'] = False; entry_type = 'OB'; break

    # 2. FVG fill (structure + directional close) - MEDIUM (weight=1.0)
    if sig == 0:
        for fi in range(len(fvgs)):
            fv = fvgs[fi]
            if fv['d'] == 1 and struct >= 0:
                if L[p] <= fv['hi'] and C[p] > fv['lo'] and C[p] > O[p]:
                    sig = 1; sig_weight = 1.0; fvgs.pop(fi); entry_type = 'FVG'; break
            elif fv['d'] == -1 and struct <= 0:
                if H[p] >= fv['lo'] and C[p] < fv['hi'] and C[p] < O[p]:
                    sig = -1; sig_weight = 1.0; fvgs.pop(fi); entry_type = 'FVG'; break

    # 3. Sweep reversal (rejection + reclaim) - WEAKEST (weight=0.8)
    if sig == 0 and sl_list and struct >= 0:
        for _si, sv in sl_list[-3:]:
            if L[p] < sv and C[p] > sv:
                if _is_rejection_candle_live(O[p], H[p], L[p], C[p], 1, strict=True):
                    sig = 1; sig_weight = 0.8; entry_type = 'Sweep'; break
    if sig == 0 and sh_list and struct <= 0:
        for _si, sv in sh_list[-3:]:
            if H[p] > sv and C[p] < sv:
                if _is_rejection_candle_live(O[p], H[p], L[p], C[p], -1, strict=True):
                    sig = -1; sig_weight = 0.8; entry_type = 'Sweep'; break

    if sig == 0:
        return None

    # ── Confluence gate (WEIGHTED - EXACT BACKTEST MATCH) ──
    conf = 0.0
    if (sig == 1 and ema_bull) or (sig == -1 and ema_bear):
        conf += 1.2                         # EMA trend aligned (boost)
    if (sig == 1 and in_discount) or (sig == -1 and in_premium):
        conf += 1.0                         # correct zone
    if 20 < rsi_val < 80:
        conf += 0.8                         # RSI healthy (avoid extremes)
    if adx_val >= adx_th + 8:
        conf += 0.8                         # strong trend bonus
    
    # Market regime: high ADX means less confluence needed
    regime_boost = 0.0
    if adx_val >= 35:
        regime_boost = 1.0  # Strong trend: relax confluence by 1.0
    elif adx_val < 15:
        regime_boost = -0.5  # Weak trend: tighten by 0.5
    
    conf += sig_weight + regime_boost
    
    # Per-instrument confluence gates (matches backtest exactly)
    min_conf = get_min_confluence(symbol)
    
    # Strong trend confluence relaxation (matches backtest)
    if adx_val > 40:
        min_conf = max(0.8, min_conf - 0.5)  # Strong: modest relax
    elif adx_val > 30:
        min_conf = max(0.8, min_conf - 0.3)  # Good trend: light relax
    elif adx_val > 25:
        min_conf = max(0.8, min_conf - 0.1)  # Moderate trend: tiny relax
    
    if conf < min_conf:
        return None

    # ── Build signal ──
    if sig == 1:
        entry = ask
        stop = entry - (atr_mult * atr_val)
        tp = entry + (atr_mult * atr_val * rr_ratio)
    else:
        entry = bid
        stop = entry + (atr_mult * atr_val)
        tp = entry - (atr_mult * atr_val * rr_ratio)

    # Sanity check
    stop_dist = abs(entry - stop)
    if stop_dist < tick_size * 10 or stop_dist > atr_val * 6:
        return None

    signal_result = {
        'symbol': symbol,
        'broker_symbol': sym_info['broker_symbol'],
        'direction': 'BUY' if sig == 1 else 'SELL',
        'entry': round(entry, digits),
        'stop': round(stop, digits),
        'tp': round(tp, digits),
        'bid': bid, 'ask': ask,
        'spread': spread_points,
        'adx': round(adx_val, 2),
        'atr': round(atr_val, digits),
        'atr_mult': atr_mult,
        'ema_fast': round(float(ema_f_p), digits),
        'ema_slow': round(float(ema_s_p), digits),
        'timestamp': bar_time,
        'confluence_score': conf,
        'entry_type': entry_type,
        'structure': struct,
        'params': f"ICT EMA{fast}/{slow} ADX>{adx_th} ATR×{atr_mult} RR={rr_ratio} {entry_type} conf={conf}",
    }
    return signal_result


def generate_smart_money_signal(df_1h: pd.DataFrame, df_5m: pd.DataFrame, 
                                 symbol: str, sym_info: dict) -> dict | None:
    """
    Smart Money Strategy - Liquidity Sweep + MSS + Pullback Entry
    HTF Bias (1H) → LTF Entry (5m)
    """
    from smart_money_strategy import SmartMoneyStrategy, should_trade, get_session_filter
    
    # Check session filter first (London 07-11, NY 13-17)
    if not get_session_filter():
        return None
    
    # Check trade limits
    today = datetime.now().strftime('%Y-%m-%d')
    daily_trades = daily_trade_count.get(symbol, {}).get(today, 0)
    daily_loss_count = consecutive_losses.get(symbol, 0)
    
    # Use simple balance check for DD
    try:
        if symbol in symbol_virtual_balance and symbol in symbol_day_start_balance:
            day_start = float(symbol_day_start_balance[symbol])
            current = float(symbol_virtual_balance[symbol])
            dd_pct = (day_start - current) / day_start * 100 if day_start > 0 else 0
        else:
            dd_pct = 0
    except:
        dd_pct = 0
    
    if not should_trade(symbol, daily_trades, daily_loss_count, dd_pct):
        return None
    
    # Create strategy instance
    strategy = SmartMoneyStrategy(df_1h, df_5m, symbol)
    
    # Check for signal
    signal = strategy.check_signal()
    if signal is None:
        return None
    
    # Build result
    bid = sym_info['bid']
    ask = sym_info['ask']
    digits = sym_info['digits']
    
    direction = signal['direction']
    entry = signal['entry']
    stop = signal['stop']
    target = signal['target']
    
    return {
        'symbol': symbol,
        'broker_symbol': sym_info['broker_symbol'],
        'direction': 'BUY' if direction == 'buy' else 'SELL',
        'entry': round(entry, digits),
        'stop': round(stop, digits),
        'tp': round(target, digits),
        'bid': bid,
        'ask': ask,
        'spread': sym_info['spread'],
        'timestamp': datetime.now(),
        'confluence_score': 2.0,  # Smart money is high quality
        'entry_type': 'SmartMoney',
        'structure': 1 if direction == 'buy' else -1,
        'params': f"SmartMoney {direction} bias={signal['bias']}",
    }


# runtime config

def load_config() -> dict:
    """Load runtime config from file."""
    cfg = {
        'risk_percent': DEFAULT_RISK,
        'enabled_symbols': SYMBOLS.copy(),
        'max_daily_drawdown_pct': MAX_DAILY_DRAWDOWN,
        'max_margin_usage_pct': MAX_MARGIN_USAGE,
        'daily_drawdown_adjustment_usd': 0.0,
    }
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            cfg.update(data)
        except Exception:
            pass
    cfg['risk_percent'] = max(MIN_RUNTIME_RISK, min(MAX_RUNTIME_RISK, float(cfg.get('risk_percent', DEFAULT_RISK))))
    cfg['enabled_symbols'] = [s for s in cfg.get('enabled_symbols', SYMBOLS) if s in SYMBOLS]
    cfg['max_daily_drawdown_pct'] = max(0.5, min(25.0, float(cfg.get('max_daily_drawdown_pct', MAX_DAILY_DRAWDOWN))))
    cfg['max_margin_usage_pct'] = max(5.0, min(95.0, float(cfg.get('max_margin_usage_pct', MAX_MARGIN_USAGE))))
    cfg['daily_drawdown_adjustment_usd'] = float(cfg.get('daily_drawdown_adjustment_usd', 0.0))

    # Reload remote dashboard push config on each scan cycle
    global _dashboard_cfg
    _dashboard_cfg = _load_dashboard_push_config()

    return cfg


def save_config(cfg: dict):
    """Save runtime config to file."""
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


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
                            params = get_instrument_settings(sym)
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
                            params = get_instrument_settings(sym)
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

                # /risk command
                elif text.startswith('/risk'):
                    parts = text.split()
                    if len(parts) >= 2:
                        try:
                            val = float(parts[1])
                            if MIN_RUNTIME_RISK <= val <= MAX_RUNTIME_RISK:
                                cfg['risk_percent'] = round(val, 2)
                                send_telegram_message(f"✅ <b>Risk Updated</b>\n{cfg['risk_percent']}% per trade")
                                changed = True
                            else:
                                send_telegram_message(f"❌ Risk must be between {MIN_RUNTIME_RISK:.2f}-{MAX_RUNTIME_RISK:.2f}%\nExample: /risk 0.5")
                        except ValueError:
                            send_telegram_message("❌ Invalid format\nExample: /risk 0.5")
                    else:
                        send_telegram_message(f"📊 <b>Current Risk</b>\n{cfg['risk_percent']}% per trade\n\nTo change: /risk [value]")
                
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
                        f"🛑 Daily DD Limit: {cfg.get('max_daily_drawdown_pct', MAX_DAILY_DRAWDOWN):.2f}%\n"
                        f"📉 DD Adjustment: ${cfg.get('daily_drawdown_adjustment_usd', 0.0):.2f}\n"
                        f"🧱 Margin Cap: {cfg.get('max_margin_usage_pct', MAX_MARGIN_USAGE):.1f}%\n"
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
    """Process a single symbol and return its status and signal."""
    global last_signals, tracked_positions
    
    if symbol not in enabled:
        return (symbol, None, None)
    
    # Get REAL symbol info from MT5
    sym_info = get_symbol_info(symbol)
    if not sym_info:
        return (symbol, f"{symbol}:ERR", None)
    
    # Check if we already have an open position for this symbol
    existing_pos = get_position_for_symbol(symbol)
    
    # Add entry_regime from tracked_positions if available
    if existing_pos and symbol in tracked_positions:
        existing_pos['entry_regime'] = tracked_positions[symbol].get('entry_regime', 'unknown')
    
    # Get strategy params from best_settings.json (baseline)
    params = get_instrument_settings(symbol)
    if not params:
        return (symbol, f"{symbol}:NOCFG", None)
    
    # Parity mode: do NOT override backtest settings with live-learned params.
    
    # Fetch LIVE candles
    df = fetch_live_candles(symbol)
    if df is None or len(df) < 100:
        return (symbol, f"{symbol}:NODATA", None)
    
    # Generate signal - Choose strategy
    if USE_SMART_MONEY_STRATEGY:
        # Smart Money Strategy: Liquidity Sweep + MSS + Pullback
        df_1h = fetch_live_candles(symbol, timeframe=mt5.TIMEFRAME_H1, bars=200)
        df_5m = fetch_live_candles(symbol, timeframe=mt5.TIMEFRAME_M5, bars=300)
        if df_1h is not None and df_5m is not None:
            signal = generate_smart_money_signal(df_1h, df_5m, symbol, sym_info)
        else:
            signal = None
    else:
        # Original ICT/SMC strategy
        signal = generate_signal(df, params, symbol, sym_info)
    
    # Fixed-rule signal validation
    
    # If we have a position → MANAGEMENT state
    if existing_pos:
        set_state(BotState.MANAGEMENT)
        
        # ── SAFETY: Max Hold Time (matching backtest: 96 bars forex, 120 crypto) ──
        try:
            open_time_str = tracked_positions.get(symbol, {}).get('open_time')
            if open_time_str:
                open_dt = datetime.strptime(open_time_str, '%Y-%m-%d %H:%M:%S')
                elapsed_min = (datetime.now() - open_dt).total_seconds() / 60
                is_crypto_or_index = symbol in ('BTCUSD', 'NAS100')
                max_hold = MAX_HOLD_MINUTES_CRYPTO if is_crypto_or_index else MAX_HOLD_MINUTES_FOREX
                if elapsed_min >= max_hold:
                    print(f"⏰ {symbol}: Max hold time exceeded ({elapsed_min:.0f} min > {max_hold} min) — force closing")
                    log_event(f"{symbol}: Max hold time close after {elapsed_min:.0f} min", "INFO")
                    send_telegram_message(
                        f"⏰ <b>Max Hold Time</b>\n"
                        f"{symbol}: Position held {elapsed_min:.0f} min (limit: {max_hold})\n"
                        f"P/L: ${existing_pos['profit']:.2f}\n"
                        f"Auto-closing position"
                    )
                    if close_position(existing_pos):
                        return (symbol, f"{symbol}:TIME_CLOSE", None)
        except Exception as e:
            print(f"[!] Max hold time check error {symbol}: {e}")
        pl = existing_pos['profit']
        
        # ── BREAK-EVEN & TRAILING STOP (per documentation) ──────────
        # Rule 1: At 1R profit → SL moves to entry price (break-even)
        # Rule 2: At 50 % of target profit → SL moves to 25 % profit level
        tracked = tracked_positions.get(symbol, {})
        entry_price = tracked.get('entry_price') or existing_pos.get('open_price', 0)
        original_sl = tracked.get('original_sl') or existing_pos.get('sl', 0)
        original_tp = tracked.get('original_tp') or existing_pos.get('tp', 0)
        current_sl = existing_pos.get('sl', original_sl)
        be_stage = tracked.get('be_stage', 0)  # 0=none, 1=BE done, 2=trail done

        if entry_price and original_sl and original_tp:
            initial_risk = abs(entry_price - original_sl)
            target_profit = abs(original_tp - entry_price)
            current_price = sym_info['bid'] if existing_pos['direction'] == 'BUY' else sym_info['ask']
            digits = sym_info.get('digits', 5)

            if existing_pos['direction'] == 'BUY':
                unrealized = current_price - entry_price
            else:
                unrealized = entry_price - current_price

            # Stage 1: At 0.8R profit → break-even (SL = entry) - MATCH BACKTEST
            if be_stage < 1 and initial_risk > 0 and unrealized >= initial_risk * 0.8:
                new_sl = round(entry_price, digits)
                if (existing_pos['direction'] == 'BUY' and new_sl > current_sl) or \
                   (existing_pos['direction'] == 'SELL' and new_sl < current_sl):
                    if modify_position_sl_tp(existing_pos, new_sl=new_sl):
                        tracked_positions.setdefault(symbol, {})['be_stage'] = 1
                        tracked_positions[symbol]['sl'] = new_sl
                        log_event(f"{symbol}: Break-even SL moved to {new_sl}", "INFO")
                        send_telegram_message(
                            f"🛡️ <b>Break-Even</b>\n"
                            f"{symbol}: SL → {new_sl} (entry price)\n"
                            f"Unrealized: ${unrealized:.2f}"
                        )
                        be_stage = 1

            # Stage 2: At 50% of target → SL moves to 40% profit level (MATCH BACKTEST)
            if be_stage < 2 and target_profit > 0 and unrealized >= target_profit * 0.5:
                if existing_pos['direction'] == 'BUY':
                    new_sl = round(entry_price + target_profit * 0.4, digits)
                else:
                    new_sl = round(entry_price - target_profit * 0.4, digits)
                if (existing_pos['direction'] == 'BUY' and new_sl > current_sl) or \
                   (existing_pos['direction'] == 'SELL' and new_sl < current_sl):
                    if modify_position_sl_tp(existing_pos, new_sl=new_sl):
                        tracked_positions.setdefault(symbol, {})['be_stage'] = 2
                        tracked_positions[symbol]['sl'] = new_sl
                        log_event(f"{symbol}: Trailing SL moved to {new_sl} (40% profit)", "INFO")
                        send_telegram_message(
                            f"📈 <b>Trailing Stop</b>\n"
                            f"{symbol}: SL → {new_sl} (40% profit lock)\n"
                            f"Unrealized: ${unrealized:.2f}"
                        )

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
        return (symbol, f"{symbol}:{dir_char}{pl_str}", None)
    
    # No existing position - check for new signal
    if signal:
        sig_key = f"{symbol}_{signal['direction']}"
        # Signal spacing REMOVED for maximum profit (matches backtest)
        # last_time = last_signals.get(sig_key)
        # if last_time and (datetime.now(timezone.utc) - last_time).seconds < 60:
        #     return (symbol, f"{symbol}:WAIT", None)
        
        # Return signal for execution
        return (symbol, None, signal)
    else:
        return (symbol, f"{symbol}:-", None)


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
    risk = cfg.get('risk_percent', DEFAULT_RISK)
    # Backtest parity mode: fixed 3% daily DD threshold (same as backtest code)
    max_daily_dd = BACKTEST_DD_LIMIT_PCT
    max_margin_usage = float(cfg.get('max_margin_usage_pct', MAX_MARGIN_USAGE))
    drawdown_adjustment = float(cfg.get('daily_drawdown_adjustment_usd', 0.0))
    
    # First, check for closed positions (SL/TP hit) - keep this sequential
    current_positions = {pos.symbol: pos for pos in get_open_positions()}
    
    for symbol, prev_pos in list(tracked_positions.items()):
        if symbol not in current_positions:
            # Position was closed (SL or TP hit)
            from_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
            to_date = datetime.now(timezone.utc)
            
            deals = mt5.history_deals_get(from_date, to_date)
            if deals:
                for deal in reversed(deals):
                    if deal.symbol == symbol and deal.position_id == prev_pos.get('ticket'):
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
                        
                        # Consecutive loss tracking
                        if profit < 0:
                            consecutive_losses[symbol] = consecutive_losses.get(symbol, 0) + 1
                            if consecutive_losses[symbol] >= 3:
                                blocked_symbols[symbol] = today_str
                                print(f"🚫 {symbol}: 3 consecutive losses — blocked for today")
                                log_event(f"{symbol}: Blocked after 3 consecutive losses", "WARN")
                                send_telegram_message(
                                    f"🚫 <b>Symbol Blocked</b>\n"
                                    f"{symbol}: 3 consecutive losses\n"
                                    f"Blocked until tomorrow"
                                )
                        else:
                            consecutive_losses[symbol] = 0  # Win resets counter

                        # Backtest-parity daily drawdown state update (per symbol virtual balance)
                        if symbol not in symbol_virtual_balance:
                            symbol_virtual_balance[symbol] = BACKTEST_INITIAL_BALANCE
                        symbol_virtual_balance[symbol] = float(symbol_virtual_balance[symbol]) + float(profit)
                        
                        # Post-SL cooldown DISABLED - matches backtest exactly
                        # if exit_reason == 'SL':
                        #     sl_cooldown_until[symbol] = datetime.now(timezone.utc) + timedelta(minutes=SL_COOLDOWN_MINUTES)
                        #     print(f"⏳ {symbol}: SL cooldown until {sl_cooldown_until[symbol].strftime('%H:%M')}")
                        
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
            symbol, status_str, signal = future.result()
            
            if status_str:
                status.append(status_str)
            
            if signal:
                signals_to_execute.append((symbol, signal))
    
    # Execute any new signals (sequential for safety)
    for symbol, signal in signals_to_execute:
        set_state(BotState.EXECUTION)
        
        today_str = datetime.now().strftime('%Y-%m-%d')

        # Backtest-parity daily reset (per symbol)
        if symbol_day_marker.get(symbol) != today_str:
            symbol_day_marker[symbol] = today_str
            if symbol not in symbol_virtual_balance:
                symbol_virtual_balance[symbol] = BACKTEST_INITIAL_BALANCE
            symbol_day_start_balance[symbol] = float(symbol_virtual_balance[symbol])

        # ── SAFETY: Reset daily blocks at new day ───────────────────
        # Clear blocks for symbols that were blocked on a previous day
        for sym in list(blocked_symbols.keys()):
            if blocked_symbols[sym] != today_str:
                del blocked_symbols[sym]
                consecutive_losses[sym] = 0

        # ── SAFETY: Consecutive Loss Blocker (3 losses → block for day) ──
        if symbol in blocked_symbols and blocked_symbols[symbol] == today_str:
            print(f"🚫 {symbol}: Blocked after 3 consecutive losses — skipping")
            status.append(f"{symbol}:BLOCKED_LOSSES")
            continue

        # Post-SL cooldown DISABLED - matches backtest exactly for maximum profit
        # if symbol in sl_cooldown_until:
        #     if datetime.now(timezone.utc) < sl_cooldown_until[symbol]:
        #         remaining = (sl_cooldown_until[symbol] - datetime.now(timezone.utc)).total_seconds() / 60
        #         print(f"⏳ {symbol}: SL cooldown — {remaining:.0f} min remaining")
        #         status.append(f"{symbol}:COOLDOWN")
        #         continue
        #     else:
        #         del sl_cooldown_until[symbol]  # Cooldown expired

        # ── SAFETY: Daily Trade Cap (DISABLED - matches backtest exactly) ─────
        # Daily cap removed - backtest has no daily restrictions
        # is_crypto = symbol in ('BTCUSD',)
        # max_daily = 100  # Increased from 6/8 for maximum trade volume
        # sym_daily = daily_trade_count.get(symbol, {})
        # trades_today = sym_daily.get(today_str, 0)
        # if trades_today >= max_daily:
        #     print(f"📊 {symbol}: Daily trade cap reached ({trades_today}/{max_daily}) — skipping")
        #     status.append(f"{symbol}:DAILY_CAP")
        #     continue

        # ── SAFETY: Daily Drawdown Block (match backtest per-symbol logic) ───
        try:
            day_start_bal = float(symbol_day_start_balance.get(symbol, BACKTEST_INITIAL_BALANCE))
            curr_bal = float(symbol_virtual_balance.get(symbol, BACKTEST_INITIAL_BALANCE))
            if day_start_bal > 0:
                dd_pct = (day_start_bal - curr_bal) / day_start_bal * 100.0
                if dd_pct >= float(max_daily_dd):
                    blocked_symbols[symbol] = today_str
                    print(f"🚫 {symbol}: Daily DD {dd_pct:.2f}% >= {max_daily_dd:.2f}% — blocked for today")
                    status.append(f"{symbol}:BLOCKED_DD")
                    continue
        except Exception:
            pass

        # ── SAFETY: Margin Protection (20 %) ────────────────────────
        # Doc: "ak je viac ako 20 % kapitálu viazaného v marži, nový príkaz sa zablokuje"
        # Margin limit check DISABLED - user wants maximum margin usage
        # try:
        #     account = mt5.account_info()
        #     if account and account.balance > 0:
        #         margin_used_pct = (account.balance - account.margin_free) / account.balance * 100
        #         if margin_used_pct > max_margin_usage:
        #             print(f"[!] MARGIN LIMIT: {margin_used_pct:.1f}% margin used > {max_margin_usage}% — blocking new order")
        #             log_event(f"Margin protection: {margin_used_pct:.1f}% used", "WARN")
        #             status.append(f"{symbol}:MARGIN")
        #             continue
        # except Exception:
            pass

        # NEW SIGNAL - this is important, print it
        sym_info = get_symbol_info(symbol)
        dir_char = '▲' if signal['direction'] == 'BUY' else '▼'
        sig_line = f">>> NEW SIGNAL: {dir_char} {signal['direction']} {symbol} @ {signal['entry']} | TP:{signal['tp']} SL:{signal['stop']}"
        print(f"\n{sig_line}")
        _push_logs_async([sig_line])
        
        # Backtest parity: disable HTF confirmation gate and adaptive risk scaling.
        htf_bias = 'N/A'
        risk_used = float(risk)

        # Don't send signal alert - will send when position actually opens
        success = open_position_with_retry(signal, sym_info, risk_used)
        
        if success:
            sig_key = f"{symbol}_{signal['direction']}"
            last_signals[sig_key] = datetime.now(timezone.utc)
            status.append(f"{symbol}:OPENED")
            
            # ── Increment daily trade counter ─────────────────────────
            daily_trade_count.setdefault(symbol, {})
            daily_trade_count[symbol][today_str] = daily_trade_count[symbol].get(today_str, 0) + 1
            
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
                    'be_stage': 0,  # 0=none, 1=BE done, 2=trail done
                }
                
                # ── SQLite: record trade open ──
                try:
                    insert_trade(
                        ticket=pos.get('ticket', 0),
                        symbol=symbol,
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
    print(f"[{ts}] {' | '.join(sorted_status)}")

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
    parser.add_argument('--loop', type=int, default=10, help='Seconds between scans (default: 10)')
    args = parser.parse_args()

    print("=" * 60)
    print("  ULTIMA TRADING BOT — ICT / SMC Engine")
    print("=" * 60)

    # Initialize SQLite database (trades, order_blocks, logs)
    init_trading_db()
    log_event("Bot process starting", "INFO")

    # Initialize MT5
    if not init_mt5():
        print("[!] Cannot start without MT5 connection")
        update_runtime_status(state='error', message='Cannot start without MT5 connection')
        return
    
    print(f"[✓] MT5 connected: {mt5.terminal_info().name}")
    print(f"[✓] Account: {mt5.account_info().login}")
    update_runtime_status(state='starting', message='Bot process initialized')
    
    # Load config
    cfg = load_config()
    print(f"[✓] Risk: {cfg['risk_percent']}%")
    print(f"[✓] Symbols: {', '.join(cfg['enabled_symbols'])}")
    
    # Load baseline strategy params
    baseline_params = {}
    for sym in SYMBOLS:
        params = get_instrument_settings(sym)
        if params:
            baseline_params[sym] = params
    
    # Telegram bot for commands
    tg = TelegramBot()
    if tg.is_configured():
        print("[✓] Telegram connected")
    else:
        print("[!] Telegram not configured")
    
    # Pure ICT/SMC strategy execution
    
    # Show existing open positions
    show_open_positions()
    
    print("=" * 60)

    try:
        if args.once:
            cfg = load_config()
            scan_markets(cfg)
        else:
            interval = max(2, args.loop)
            print(f"[+] Scanning every {interval}s (Ctrl+C to stop)\n")
            
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
            

            
            # Market scanning loop
            while True:
                start = time.time()
                try:
                    cfg = load_config()
                    scan_markets(cfg)
                except Exception as e:
                    print(f"[!] Scan loop error: {e}")
                    update_runtime_status(state='error', message=f"Scan loop error: {e}")
                elapsed = time.time() - start
                time.sleep(max(1, interval - elapsed))
    except KeyboardInterrupt:
        print("\n[!] Stopped by user")
        update_runtime_status(state='stopped', message='Stopped by user')
        if not args.once:
            telegram_active.clear()
    finally:
        shutdown_mt5()
        update_runtime_status(state='stopped', message='MT5 disconnected')
        print("[✓] MT5 disconnected")


if __name__ == '__main__':
    main()
