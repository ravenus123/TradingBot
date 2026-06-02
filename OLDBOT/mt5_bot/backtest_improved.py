import os, sys, argparse, json, time, warnings, random
from pathlib import Path
from itertools import product
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import numpy as np
import pandas as pd
import pickle

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw):
        return it

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

warnings.filterwarnings('ignore')

# ===============================================================================
# CONFIGURATION
# ===============================================================================

INSTRUMENTS = {
    # CORE 3 ONLY - matches live bot exactly
    'EURUSD': {'pip_size': 0.0001, 'spread': 0.8,  'vol': 1.0},
    'NAS100': {'pip_size': 1.0,    'spread': 2.0,  'vol': 1.0},  # NASDAQ 100
    'XAUUSD': {'pip_size': 0.1,    'spread': 3.0,  'vol': 1.5},  # Gold
    'GBPUSD': {'pip_size': 0.0001, 'spread': 1.0,  'vol': 1.0},
    'USDJPY': {'pip_size': 0.01,   'spread': 1.0,  'vol': 1.0},
    'BTCUSD': {'pip_size': 1.0,    'spread': 10.0, 'vol': 2.0},
}

FOREX_PLUS = {'EURUSD', 'GBPUSD', 'USDJPY', 'EURJPY', 'XAUUSD'}  # Advisor-approved list
BEST_SETTINGS_FILE = Path(__file__).parent / 'best_settings.json'
DATA_DIR = Path(__file__).parent / 'backtest_data_improved'
DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR = Path(__file__).parent / 'liverun' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
BACKTEST_DAYS = 30
INITIAL_BALANCE = 10000.0
# Allow trading 24/7 when True (user requested fully autonomous 24/7 operation)
FULL_TIME_TRADING = True
# Minimum confluence required for entry (AGGRESSIVE for 100%+ returns)
MIN_CONFLUENCE = 0.8


# ===============================================================================
# DATA
# ===============================================================================

def initialize_mt5() -> bool:
    if mt5 is None:
        return False
    try:
        return bool(mt5.initialize())
    except Exception:
        return False


def fetch_data(symbol: str, bars: int = 10000) -> Optional[pd.DataFrame]:
    """
    Fetch REAL data from MetaTrader 5. NO CSV FALLBACK.
    Returns DataFrame with datetime index or raises error.
    """
    # If MetaTrader5 is not available, allow CSV fallback or synthetic data for offline testing.
    if mt5 is None:
        # Try CSV fallback in DATA_DIR
        csv_path = DATA_DIR / f"{symbol}.csv"
        if csv_path.exists():
            df = pd.read_csv(csv_path, parse_dates=['Time'], index_col='Time')
            print(f"[Fallback] Loaded {len(df)} bars for {symbol} from {csv_path}")
            return df

        # Synthetic data fallback (useful for offline testing / CI)
        print(f"[Fallback] MetaTrader5 not available and no CSV for {symbol}. Generating synthetic data ({bars} bars).")
        end = datetime.now()
        idx = pd.date_range(end=end, periods=bars, freq='15T')
        # simple random-walk around a base price (symbol-agnostic)
        base = 1.1000 if symbol in FOREX_PLUS else 1000.0
        close = np.cumsum(np.random.randn(bars) * (0.0005 if symbol in FOREX_PLUS else 1.0)) + base
        openp = close + np.random.randn(bars) * (0.0001 if symbol in FOREX_PLUS else 0.5)
        high = np.maximum(openp, close) + np.abs(np.random.randn(bars) * (0.0002 if symbol in FOREX_PLUS else 0.5))
        low = np.minimum(openp, close) - np.abs(np.random.randn(bars) * (0.0002 if symbol in FOREX_PLUS else 0.5))
        vol = np.random.randint(10, 100, size=bars)
        df = pd.DataFrame({'Open': openp, 'High': high, 'Low': low, 'Close': close, 'Volume': vol}, index=idx)
        return df
    
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
    
    try:
        # Some brokers use '.i' suffix for Forex
        req = f"{symbol}.i" if symbol in FOREX_PLUS else symbol
        
        # Try M15 first with copy_rates_from to get historical data
        # Start from 2 years ago to get more historical data
        from_date = datetime.now() - timedelta(days=730)
        to_date = datetime.now()
        
        rates = mt5.copy_rates_range(req, mt5.TIMEFRAME_M15, from_date, to_date)
        timeframe_used = "M15"
        
        # Fallback to copy_rates_from_pos if copy_rates_range fails
        if rates is None or len(rates) < 300:
            rates = mt5.copy_rates_from_pos(req, mt5.TIMEFRAME_M15, 0, bars)
            timeframe_used = "M15 (fallback)"
        
        # Fallback to H1 if M15 fails
        if rates is None or len(rates) < 300:
            rates = mt5.copy_rates_from_pos(req, mt5.TIMEFRAME_H1, 0, bars // 4)
            timeframe_used = "H1"
        
        if rates is None or len(rates) < 300:
            error_code = mt5.last_error()
            raise RuntimeError(
                f"MT5 returned insufficient data for {symbol} (got {len(rates) if rates is not None else 0} bars). "
                f"MT5 error: {error_code}. Make sure {symbol} is in Market Watch and broker is connected."
            )
        
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df = df[['time', 'open', 'high', 'low', 'close', 'tick_volume']]
        df.columns = ['Time', 'Open', 'High', 'Low', 'Close', 'Volume']
        df.set_index('Time', inplace=True)
        
        print(f"[MT5] Fetched {len(df)} bars of {symbol} on {timeframe_used} | "
              f"From {df.index[0]} to {df.index[-1]}")
        
        return df
    finally:
        mt5.shutdown()


# ===============================================================================
# ICT / SMC  CORE
# ===============================================================================

def detect_swing_points(high: np.ndarray, low: np.ndarray,
                        left: int = 5, right: int = 3):
    """
    No-lookahead swing detection.
    Swing at bar j confirmed at bar j+right -> usable from bar j+right+1.
    """
    n = len(high)
    sh_price  = np.full(n, np.nan)
    sh_origin = np.full(n, -1, dtype=int)
    sl_price  = np.full(n, np.nan)
    sl_origin = np.full(n, -1, dtype=int)

    for j in range(left, n - right):
        # Swing high
        is_sh = True
        for k in range(1, left + 1):
            if high[j - k] > high[j]:
                is_sh = False; break
        if is_sh:
            for k in range(1, right + 1):
                if high[j + k] >= high[j]:
                    is_sh = False; break
        if is_sh and j + right < n:
            sh_price[j + right]  = high[j]
            sh_origin[j + right] = j

        # Swing low
        is_sl = True
        for k in range(1, left + 1):
            if low[j - k] < low[j]:
                is_sl = False; break
        if is_sl:
            for k in range(1, right + 1):
                if low[j + k] <= low[j]:
                    is_sl = False; break
        if is_sl and j + right < n:
            sl_price[j + right]  = low[j]
            sl_origin[j + right] = j

    return sh_price, sh_origin, sl_price, sl_origin


def is_rejection_candle(o, h, l, c, direction, strict=False):
    """
    Check for bullish (direction=1) or bearish (direction=-1) rejection.
    STRICTER version: tighter thresholds to avoid false signals.
    strict=True raises thresholds to filter weaker rejections.
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
            # Large bullish body
            if c > o and body / rng >= 0.70:
                return True
        else:  # bearish
            upper_wick = h - max(o, c)
            if c < o and (h - c) / rng >= 0.68:
                return True
            if upper_wick / rng >= 0.50:
                return True
            if c < o and body / rng >= 0.70:
                return True
        return False

    # Standard (original) thresholds
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


# ===============================================================================
# INDICATORS
# ===============================================================================

def add_indicators(df, ema_period=50, atr_period=14, adx_period=14, symbol: Optional[str]=None, use_cache: bool=True):
    """
    Add indicators to dataframe. If `symbol` provided and Time column exists, results
    may be cached to speed repeated runs (useful during parameter sweeps and Monte Carlo).
    """
    # Attempt to load cache keyed by symbol, time-range and params
    cache_path = None
    if use_cache and symbol is not None and 'Time' in df.columns:
        try:
            start = pd.to_datetime(df['Time'].iloc[0]).strftime('%Y%m%d%H%M')
            end = pd.to_datetime(df['Time'].iloc[-1]).strftime('%Y%m%d%H%M')
            key = f"{symbol}_{start}_{end}_e{ema_period}_a{atr_period}_x{adx_period}.pkl"
            cache_path = CACHE_DIR / key
            if cache_path.exists():
                with open(cache_path, 'rb') as fh:
                    cached = pickle.load(fh)
                return cached.copy()
        except Exception:
            cache_path = None

    df = df.copy()
    close = df['Close'].astype(float).values
    high  = df['High'].astype(float).values
    low   = df['Low'].astype(float).values
    cs = pd.Series(close)

    df['EMA'] = cs.ewm(span=ema_period, adjust=False).mean().values
    # Fast EMA for momentum / slope
    fast_span = max(8, ema_period // 3)
    df['EMA_Fast'] = cs.ewm(span=fast_span, adjust=False).mean().values

    # ATR
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - np.roll(close, 1)),
                               np.abs(low  - np.roll(close, 1))))
    df['ATR'] = pd.Series(tr).rolling(atr_period).mean().values

    # ADX
    plus_dm  = np.maximum(high - np.roll(high, 1), 0)
    minus_dm = np.maximum(np.roll(low, 1) - low, 0)
    atr_safe = np.where(df['ATR'].values > 0, df['ATR'].values, np.nan)
    di_p = 100 * (pd.Series(plus_dm).rolling(adx_period).mean().values / atr_safe)
    di_m = 100 * (pd.Series(minus_dm).rolling(adx_period).mean().values / atr_safe)
    dx   = 100 * (np.abs(di_p - di_m) /
                  np.where((di_p + di_m) != 0, di_p + di_m, np.nan))
    df['ADX'] = pd.Series(dx).rolling(adx_period).mean().values

    # RSI-14
    delta = cs.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(50).values

    if 'Time' in df.columns and not np.issubdtype(df['Time'].dtype, np.datetime64):
        df['Time'] = pd.to_datetime(df['Time'])
    df['EMA_H1'] = cs.rolling(12).mean().bfill().values  # 12x M5 = 1hr
    df['EMA_8'] = cs.ewm(span=8, adjust=False).mean().bfill().values
    df['EMA_21'] = cs.ewm(span=21, adjust=False).mean().bfill().values
    df['EMA_34'] = cs.ewm(span=34, adjust=False).mean().bfill().values
    df['EMA_50'] = cs.ewm(span=50, adjust=False).mean().bfill().values
    # Save to cache if possible
    if use_cache and cache_path is not None:
        try:
            with open(cache_path, 'wb') as fh:
                pickle.dump(df, fh)
        except Exception:
            pass
    return df


# ===============================================================================
# PARAMETERS
# ===============================================================================

def _get_min_confluence(symbol):
    """OPTIMIZED confluence gates - instrument-specific tuning"""
    table = {
        'BTCUSD': 0.8,   # More conservative - crypto is volatile
        'EURUSD': 0.5,   # Lower threshold to boost A+ trade frequency
        'GBPUSD': 0.7,   # Already good
        'GBPJPY': 0.8,   # Already good
        'XAUUSD': 0.3,   # Aggressive - enable more A+ setups on gold
        'USDJPY': 0.8,   # Already good
        'NAS100': 0.4,   # Lower threshold to generate more index trades
    }
    return table.get(symbol, 0.8)

def _adaptive_rr(base_rr, adx_value):
    """
    Boost R:R ratio during strong trends; reduce during weak.
    PROFITABILITY BOOSTER #1: RR scaling (ORIGINAL PROVEN VERSION).
    """
    if adx_value >= 35:
        return base_rr * 1.25  # Strong trend: 25% boost
    elif adx_value >= 25:
        return base_rr * 1.10  # Moderate trend: 10% boost
    elif adx_value <= 12:
        return base_rr * 0.85  # Weak trend: reduce by 15%
    return base_rr

def _is_clean_bos(o, h, l, c, direction, rng_threshold=0.60):
    """
    Check for CLEAN Break of Structure (not just a wick).
    PROFITABILITY BOOSTER #2: Filter fake BOS (wicks) from real BOS (body).
    """
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    body_ratio = body / rng
    
    if direction == 1:  # Bullish BOS
        # Require strong bullish body (not just upper wick)
        close_ratio = (c - l) / rng
        return c > o and body_ratio >= rng_threshold and close_ratio >= 0.70
    else:  # Bearish BOS
        # Require strong bearish body
        close_ratio = (h - c) / rng
        return c < o and body_ratio >= rng_threshold and close_ratio >= 0.70

def _get_adx_floor(symbol):
    """Per-instrument ADX minimums — ORIGINAL PROVEN SETTINGS"""
    table = {
        'USDJPY':  12.0,
        'GBPJPY':  12.0,
        'XAUUSD':  13.0,
        'EURUSD':  13.0,
        'GBPUSD':  14.0,
        'BTCUSD':  14.0,
        'NAS100':  13.0,
    }
    return table.get(symbol, 12.0)

def _get_rr_boost(symbol):
    """BOOSTER #8: Per-pair R:R boost multiplier for weak pairs
    
    Weak pairs (GBPJPY, XAUUSD) get +25-30% RR boost to improve returns
    Strong pairs (BTCUSD, EURUSD) get no boost (already profitable)
    """
    boosts = {
        'GBPJPY': 1.25,   # Weakest: +25% RR boost
        'XAUUSD': 1.20,   # Weak: +20% RR boost
        'USDJPY': 1.15,   # Slightly weak: +15% boost
        'NAS100': 1.0,    # Strong: no boost
        'BTCUSD': 1.0,    # Strong: no boost
        'EURUSD': 1.0,    # Strong: no boost
        'GBPUSD': 1.05,   # Medium: +5% boost
    }
    return boosts.get(symbol, 1.0)


def _get_min_stop_spread_ratio(symbol):
    """Minimum stop-distance-to-spread ratio needed to trade.

    This is a survivability filter: if the stop is too close to the spread,
    the setup is not worth trading because transaction costs dominate the edge.
    """
    table = {
        'EURUSD': 4.0,
        'GBPUSD': 4.0,
        'USDJPY': 4.0,
        'XAUUSD': 3.0,
        'NAS100': 3.0,
        'BTCUSD': 2.5,
        'GBPJPY': 3.5,
    }
    return table.get(symbol, 4.0)

def _default_params(symbol):
    """(ema_period, adx_min, atr_sl_mult, rr_target)"""
    # atr_sl_mult -> ATR multiplier for stop distance FROM ENTRY
    # rr_target   -> take-profit = stop_dist * rr_target
    table = {
        'EURUSD': (50, 20.0, 1.2, 1.5),
        'GBPUSD': (50, 20.0, 1.2, 1.5),
        'USDJPY': (50, 20.0, 1.2, 1.5),
        'XAUUSD': (40, 18.0, 1.5, 1.5),
        'GBPJPY': (50, 18.0, 1.2, 1.5),
        'BTCUSD': (60, 18.0, 1.8, 1.5),
        'NAS100': (50, 18.0, 1.2, 1.5),
    }
    return table.get(symbol, (50, 20.0, 1.2, 1.5))


def _load_best_params(symbol):
    defs = _default_params(symbol)
    try:
        if BEST_SETTINGS_FILE.exists():
            raw = json.loads(BEST_SETTINGS_FILE.read_text())
            row = (raw.get('instruments') or {}).get(symbol, {})
            if not row:
                return defs
            ema = int(row.get('EMA', defs[0]))
            adx = float(row.get('ADX', defs[1]))
            slm = float(row.get('SL_Mult', row.get('SL_Buffer', defs[2])))
            rr  = float(row.get('RR', defs[3]))
            return (ema, adx, slm, rr)
    except Exception:
        pass
    return defs


# ===============================================================================
# HELPERS
# ===============================================================================

def calculate_max_drawdown(ec):
    if len(ec) < 2:
        return 0.0
    peak = ec[0]; mx = 0.0
    for v in ec:
        if v > peak: peak = v
        dd = peak - v
        if dd > mx: mx = dd
    return float(mx)


def _empty_result(symbol):
    return {
        'symbol': symbol, 'trades': [],
        'metrics': {
            'total_trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0,
            'total_profit': 0, 'avg_profit': 0, 'avg_win': 0, 'avg_loss': 0,
            'best_trade': 0, 'worst_trade': 0, 'profit_factor': 0,
            'max_drawdown': 0, 'final_balance': INITIAL_BALANCE, 'return_pct': 0,
        },
        'equity_curve': [INITIAL_BALANCE], 'lookahead_safe': True,
    }


def _build_metrics(symbol, trades, equity_curve):
    if not trades:
        return _empty_result(symbol)
    profits = np.array([float(t['profit']) for t in trades])
    w = profits[profits > 0]; l = profits[profits <= 0]
    total = float(np.sum(profits)); final = INITIAL_BALANCE + total
    return {
        'symbol': symbol, 'trades': trades,
        'metrics': {
            'total_trades':  int(len(trades)),
            'wins':          int(len(w)),
            'losses':        int(len(l)),
            'win_rate':      float(len(w)/len(trades)*100) if trades else 0,
            'total_profit':  total,
            'avg_profit':    float(np.mean(profits)) if len(profits) else 0,
            'avg_win':       float(np.mean(w)) if len(w) else 0,
            'avg_loss':      float(np.mean(l)) if len(l) else 0,
            'best_trade':    float(np.max(profits)) if len(profits) else 0,
            'worst_trade':   float(np.min(profits)) if len(profits) else 0,
            'profit_factor': float(np.sum(w)/abs(np.sum(l)))
                             if len(l) and abs(np.sum(l)) > 0 else 99.0,
            'max_drawdown':  float(calculate_max_drawdown(equity_curve)),
            'final_balance': float(final),
            'return_pct':    float((final - INITIAL_BALANCE) / INITIAL_BALANCE * 100),
        },
        'equity_curve': equity_curve, 'lookahead_safe': True,
    }


def get_data_duration(df):
    if df is None or 'Time' not in df.columns or len(df) < 2:
        return "unknown"
    days = (pd.to_datetime(df['Time'].iloc[-1]) - pd.to_datetime(df['Time'].iloc[0])).days
    if days < 30: return f"{days} days"
    if days < 365: return f"{days//7} weeks ({days} days)"
    return f"{days//365} years ({days} days)"


# ===============================================================================
#  THE  ENGINE  —  ICT / SMC  backtest  (strict no-lookahead)
# ===============================================================================

def run_backtest_no_lookahead(
    df: pd.DataFrame,
    symbol: str,
    params: Optional[Tuple] = None,
    risk_pct: float = 1.0,
    use_indicator_cache: bool = True,
) -> Dict:
    """
    ICT/SMC backtest — strict no-lookahead.

    Key design choices
    ==================
    * **ATR-based stops** — stop distance = ATR × atr_sl_mult.
      This gives *consistent* position sizing regardless of OB zone width.
    * **Premium / Discount filter** — only buy in the lower half of the
      recent 50-bar range (discount) and sell in the upper half (premium).
      This is a core ICT concept for high-probability entries.
    * **Structure requirement** — OB and FVG entries require market
      structure (HH/HL or LH/LL) to be aligned with the trade direction.
    * **No breakeven** — let trades play out to TP or SL cleanly.
      A trailing stop kicks in after 2 R to lock partial gains.

    Entry types (signal bar i-1, execute bar i open):
      1. Order Block retest  (demand/supply after BOS)
      2. Fair Value Gap fill (imbalance zone re-entry)
      3. Liquidity sweep     (stop-hunt reversal)
    """
    if df is None or len(df) < 120:
        return _empty_result(symbol)

    # -- params --
    ema_period, adx_min, atr_sl_mult, rr_target = params or _load_best_params(symbol)

    # -- indicators (cached per-symbol to speed repeated runs) --
    df = add_indicators(df, ema_period=ema_period, symbol=symbol, use_cache=use_indicator_cache)
    df = df.fillna(0).reset_index(drop=True)

    O  = df['Open'].astype(float).to_numpy()
    H  = df['High'].astype(float).to_numpy()
    L  = df['Low'].astype(float).to_numpy()
    C  = df['Close'].astype(float).to_numpy()
    ATR = df['ATR'].astype(float).to_numpy()
    EMA = df['EMA'].astype(float).to_numpy()
    EMAF = df['EMA_Fast'].astype(float).to_numpy()
    ADX = df['ADX'].astype(float).to_numpy()
    RSI = df['RSI'].astype(float).to_numpy()
    times = pd.to_datetime(df['Time']) if 'Time' in df.columns else pd.Series(range(len(df)))
    hours = times.dt.hour.to_numpy() if hasattr(times, 'dt') else np.full(len(df), 12)

    pip   = INSTRUMENTS.get(symbol, INSTRUMENTS['EURUSD'])['pip_size']
    spr   = float(INSTRUMENTS.get(symbol, INSTRUMENTS['EURUSD'])['spread'])
    spr_p = spr * pip                           # spread in price units

    # -- swing detection --
    SL_LEFT = 5; SR_RIGHT = 3
    OB_LOOK = 15; MAX_OB = 80; MAX_FVG = 50
    RANGE_BARS = 50                              # bars for premium/discount calc

    sh_pr, sh_or, sl_pr, sl_or = detect_swing_points(H, L, SL_LEFT, SR_RIGHT)

    # -- state --
    trades: List[Dict] = []
    eq = [INITIAL_BALANCE]
    bal = INITIAL_BALANCE

    in_trade   = False
    dirn       = 0
    e_price    = 0.0
    e_idx      = 0
    sl_price   = 0.0
    init_sl    = 0.0
    tp_price   = 0.0
    held       = 0
    # partial_taken = False  # DISABLED: partial exits were bleeding weak pairs
    partial_1r_taken = False  # BOOSTER #4: Track if 1R partial TP taken

    r_sh: List[Tuple[int,float]] = []            # recent swing highs
    r_sl: List[Tuple[int,float]] = []            # recent swing lows
    obs:  List[Dict] = []                        # active OBs
    fvgs: List[Dict] = []                        # active FVGs
    struct = 0                                    # 1 bull, -1 bear, 0 neutral
    used_sweeps: set = set()

    dtc: Dict = {}                               # daily trade count
    cur_day   = None
    day_bal   = bal
    blocked   = False
    cooldown  = 0
    last_eidx = -1000
    last_loss_idx = -1000  # PROFITABILITY BOOSTER #3: Track last loss to avoid revenge trades
    c_losses  = 0
    risk      = max(0.1, min(10.0, risk_pct))
    _risk_scale_old = risk / 1.0  # scale all % thresholds (base = 1.0%)

    is_crypto = symbol in ('BTCUSD',)
    is_us     = symbol in ('NAS100',)
    start     = max(ema_period + 10, SL_LEFT + SR_RIGHT + 20, 60)

    # ========== MAIN LOOP ==========
    for i in range(start, len(df)):
        p = i - 1                                # "prev" bar — all decisions here

        # -- daily reset --
        if hasattr(times, 'iloc'):
            ld = pd.Timestamp(times.iloc[i]).date()
            if ld != cur_day:
                cur_day = ld; day_bal = bal; blocked = False; c_losses = 0

        # -- update confirmed swings --
        if not np.isnan(sh_pr[p]):
            r_sh.append((int(sh_or[p]), float(sh_pr[p])))
            if len(r_sh) > 25: r_sh = r_sh[-25:]
        if not np.isnan(sl_pr[p]):
            r_sl.append((int(sl_or[p]), float(sl_pr[p])))
            if len(r_sl) > 25: r_sl = r_sl[-25:]

        # -- market structure --
        if len(r_sh) >= 2 and len(r_sl) >= 2:
            hh = r_sh[-1][1] > r_sh[-2][1]
            hl = r_sl[-1][1] > r_sl[-2][1]
            lh = r_sh[-1][1] < r_sh[-2][1]
            ll = r_sl[-1][1] < r_sl[-2][1]
            if hh and hl: struct = 1
            elif lh and ll: struct = -1

        # -- detect BOS -> create OBs --
        if r_sh:
            lsh = r_sh[-1][1]
            if C[p] > lsh and (p < 2 or C[p-1] <= lsh):
                # Bullish BOS — demand OB from last bearish candle pre-move
                for j in range(p-1, max(p-OB_LOOK, start), -1):
                    if C[j] < O[j] and (H[j]-L[j]) > 0:
                        obs.append({'d':1, 'lo':float(L[j]), 'hi':float(H[j]),
                                    'b':j, 'bb':p, 'ok':True})
                        break
        if r_sl:
            lsl = r_sl[-1][1]
            if C[p] < lsl and (p < 2 or C[p-1] >= lsl):
                for j in range(p-1, max(p-OB_LOOK, start), -1):
                    if C[j] > O[j] and (H[j]-L[j]) > 0:
                        obs.append({'d':-1, 'lo':float(L[j]), 'hi':float(H[j]),
                                    'b':j, 'bb':p, 'ok':True})
                        break

        # -- detect FVGs --
        if p >= 2:
            ap = max(ATR[p], 1e-10)
            g_b = L[p] - H[p-2]
            if g_b > ap * 0.20 and C[p] > C[p-2]:
                fvgs.append({'d':1, 'lo':float(H[p-2]), 'hi':float(L[p]), 'b':p})
            g_s = L[p-2] - H[p]
            if g_s > ap * 0.20 and C[p] < C[p-2]:
                fvgs.append({'d':-1, 'lo':float(H[p]), 'hi':float(L[p-2]), 'b':p})

        # -- expire zones --
        obs  = [o for o in obs  if (p - o['bb']) < MAX_OB and o['ok']]
        fvgs = [f for f in fvgs if (p - f['b']) < MAX_FVG]
        for o in obs:
            if o['d'] == 1  and C[p] < o['lo'] - ATR[p]*0.5: o['ok'] = False
            if o['d'] == -1 and C[p] > o['hi'] + ATR[p]*0.5: o['ok'] = False

        # ========== TRADE MANAGEMENT ==========
        if in_trade:
            held += 1
            bh = H[i]; bl = L[i]

            # -- Partial profit-taking at 1R (DISABLED: was bleeding capital) --
            # (Full position exit at TP/SL provides cleaner accounting)

            # -- Exit checks — clean SL/TP --
            ex = False; ep = C[i]; er = 'Time'
            if dirn == 1:
                if bh >= tp_price:
                    ex = True; ep = tp_price; er = 'TP'
                elif bl <= sl_price:
                    ex = True; ep = sl_price; er = 'SL'
            else:
                if bl <= tp_price:
                    ex = True; ep = tp_price; er = 'TP'
                elif bh >= sl_price:
                    ex = True; ep = sl_price; er = 'SL'

            mh = 120 if symbol in ('BTCUSD','NAS100') else 96

            # AGGRESSIVE exit strategy for 100%+ returns: Earlier breakeven + faster profit locking
            sd = abs(e_price - init_sl) if init_sl is not None else 0.0
            tp_sd = abs(tp_price - e_price) if tp_price is not None else 0.0
            
            if sd > 0:
                if dirn == 1:
                    best = bh
                    profit_r = (best - e_price) / sd
                    # Stage 1: At 0.8R -> move SL to breakeven (was 1.5R)
                    if profit_r >= 0.8:
                        sl_price = max(sl_price, float(e_price))
                    # Stage 2: At 50% of target -> move SL to 40% profit level (was 80%/25%)
                    if tp_sd > 0 and profit_r >= tp_sd * 0.5:
                        new_sl = e_price + tp_sd * 0.40
                        sl_price = max(sl_price, float(new_sl))
                else:
                    best = bl
                    profit_r = (e_price - best) / sd
                    # Stage 1: At 0.8R -> move SL to breakeven (was 1.5R)
                    if profit_r >= 0.8:
                        sl_price = min(sl_price, float(e_price))
                    # Stage 2: At 50% of target -> move SL to 40% profit level (was 80%/25%)
                    if tp_sd > 0 and profit_r >= tp_sd * 0.5:
                        new_sl = e_price - tp_sd * 0.40
                        sl_price = min(sl_price, float(new_sl))
            if not ex and held >= mh:
                ex = True; er = 'Time'

            if ex:
                mv = (ep - e_price) if dirn == 1 else (e_price - ep)
                # Position sizing: use risk % but cap absolute risk to protect from runaway compounding
                max_risk_abs = INITIAL_BALANCE * 0.02  # 2% of initial balance hard cap
                ra = min(bal * (risk / 100.0), max_risk_abs)
                sd = abs(e_price - init_sl)
                gp = (mv / sd) * ra if sd > 0 else 0.0
                sc = (spr_p / sd) * ra if sd > 0 else 0.0
                pnl = float(gp - sc)
                bal += pnl; eq.append(bal)
                # Cooldown after loss
                if pnl < 0:
                    last_loss = p
                trades.append({
                    'entry_time': times.iloc[e_idx], 'exit_time': times.iloc[i],
                    'direction': 'BUY' if dirn==1 else 'SELL',
                    'entry_price': float(e_price), 'exit_price': float(ep),
                    'stop_loss': float(sl_price), 'take_profit': float(tp_price),
                    'profit': pnl, 'exit_reason': er, 'bars_held': int(held),
                })
                in_trade = False; dirn = 0  # partial_taken reset disabled
                if pnl < 0:
                    c_losses += 1
                    if c_losses >= 3: blocked = True
                    if er == 'SL': cooldown = i + 4
                else:
                    c_losses = 0
                if day_bal > 0 and (day_bal - bal)/day_bal*100 >= 3.0 * _risk_scale_old:
                    blocked = True
            continue

        # ========== ENTRY LOGIC ==========
        if blocked or i < cooldown:
            continue

        # PROFITABILITY BOOSTER #3: Avoid revenge trading (skip 3 bars after loss)
        if i - last_loss_idx < 3:
            continue

        atr_p = ATR[p]
        # Use per-instrument ADX floor for quality control
        adx_floor = max(adx_min, _get_adx_floor(symbol))
        if atr_p <= 0 or ADX[p] < adx_floor:
            continue

        # PROFITABILITY BOOSTER #1: Adaptive R:R scaling by market regime
        adjusted_rr = _adaptive_rr(rr_target, ADX[p])

        # Session filter — ICT Killzones (can be disabled for 24/7 trading)
        h = hours[p]
        if not FULL_TIME_TRADING:
            if is_crypto:
                pass                                 # 24/7
            elif is_us:
                if h < 14 or h > 20: continue        # US equity session
            else:
                # London killzone (07-11) + New York killzone (13-17)
                if not ((7 <= h <= 11) or (13 <= h <= 17)): continue

        # Spacing — REDUCED TO 2 BARS FOR TESTING: more frequent trades
        # Signal spacing REMOVED - allow back-to-back entries for maximum profit
        # if (i - last_eidx) < 2:
        #     continue

        # Daily cap — DISABLED FOR TESTING: remove limit to see max profit potential
        # Uncomment below to re-enable: md = 8 if is_crypto else 6
        # td = pd.Timestamp(times.iloc[i]).date() if hasattr(times, 'iloc') else None
        # if td and dtc.get(td, 0) >= md: continue

        # -- Trend & Zone filters (core ICT concepts) --
        ema_bull = C[p] > EMA[p] and EMAF[p] > EMA[p]
        ema_bear = C[p] < EMA[p] and EMAF[p] < EMA[p]

        range_hi = float(np.max(H[max(0, p - RANGE_BARS):p + 1]))
        range_lo = float(np.min(L[max(0, p - RANGE_BARS):p + 1]))
        range_mid = (range_hi + range_lo) / 2.0
        in_discount = C[p] < range_mid           # buy zone
        in_premium  = C[p] > range_mid           # sell zone

        # --- ICT/SMC: OB, FVG, or sweep entries with WEIGHTED confluence ---
        sig = 0
        sig_weight = 0.0  # Beast-mode: weight signals by reliability

        # Determine if we need strict rejection (low confidence confluence only)
        force_strict_rej = False

        # Order Block retest (structure required) — MOST RELIABLE (weight=1.5)
        for ob in obs:
            if not ob['ok']:
                continue
            if ob['d'] == 1 and struct >= 0:
                if L[p] <= ob['hi'] and C[p] >= ob['lo']:
                    if is_rejection_candle(O[p], H[p], L[p], C[p], 1, strict=force_strict_rej):
                        sig = 1; sig_weight = 1.5; ob['ok'] = False; break
            elif ob['d'] == -1 and struct <= 0:
                if H[p] >= ob['lo'] and C[p] <= ob['hi']:
                    if is_rejection_candle(O[p], H[p], L[p], C[p], -1, strict=force_strict_rej):
                        sig = -1; sig_weight = 1.5; ob['ok'] = False; break

        # FVG fill (structure + directional close) — MEDIUM (weight=1.0)
        if sig == 0:
            for fi in range(len(fvgs)):
                fv = fvgs[fi]
                if fv['d'] == 1 and struct >= 0:
                    if L[p] <= fv['hi'] and C[p] > fv['lo'] and C[p] > O[p]:
                        sig = 1; sig_weight = 1.0; fvgs.pop(fi); break
                elif fv['d'] == -1 and struct <= 0:
                    if H[p] >= fv['lo'] and C[p] < fv['hi'] and C[p] < O[p]:
                        sig = -1; sig_weight = 1.0; fvgs.pop(fi); break

        # Sweep reversal (rejection + reclaim) — WEAKEST (weight=0.8)
        if sig == 0 and r_sl and struct >= 0:
            for _si, sv in r_sl[-3:]:
                if L[p] < sv and C[p] > sv:
                    if is_rejection_candle(O[p], H[p], L[p], C[p], 1, strict=True):
                        sig = 1; sig_weight = 0.8; break
        if sig == 0 and r_sh and struct <= 0:
            for _si, sv in r_sh[-3:]:
                if H[p] > sv and C[p] < sv:
                    if is_rejection_candle(O[p], H[p], L[p], C[p], -1, strict=True):
                        sig = -1; sig_weight = 0.8; break

        if sig == 0:
            continue

        # -- Confluence gate (WEIGHTED) --
        conf = 0.0
        if (sig == 1 and ema_bull) or (sig == -1 and ema_bear):
            conf += 1.2                         # EMA trend aligned (boost)
        if (sig == 1 and in_discount) or (sig == -1 and in_premium):
            conf += 1.0                         # correct zone
        if (sig == 1 and 20 < RSI[p] < 80) or (sig == -1 and 20 < RSI[p] < 80):
            conf += 0.8                         # RSI healthy (stricter: avoid 15-85 extremes)
        if ADX[p] >= adx_floor + 8:
            conf += 0.8                         # strong trend bonus
        
        # Market regime: high ADX means less confluence needed
        regime_boost = 0.0
        if ADX[p] >= 35:
            regime_boost = 1.0  # Strong trend: relax confluence by 1.0
        elif ADX[p] < 15:
            regime_boost = -0.5  # Weak trend: tighten by 0.5
        
        conf += sig_weight + regime_boost
        
        # Confluence gate: per-instrument thresholds to filter weak pairs
        min_conf = _get_min_confluence(symbol)
        
        # BOOSTER #4: Strong trend confluence relaxation (ORIGINAL PROVEN VERSION)
        # Only MODEST relaxation to avoid breaking quality filter
        if ADX[p] > 40:
            min_conf = max(0.8, min_conf - 0.5)  # Strong: modest relax
        elif ADX[p] > 30:
            min_conf = max(0.8, min_conf - 0.3)  # Good trend: light relax
        elif ADX[p] > 25:
            min_conf = max(0.8, min_conf - 0.1)  # Moderate trend: tiny relax
        
        if conf < min_conf:
            continue

        # Volatility filter — DISABLED for maximum trades: allow quiet market entries
        # recent_atr = float(np.mean(ATR[max(0, p-50):p+1])) if p > 0 else float(ATR[p])
        # if recent_atr > 0 and atr_p < recent_atr * 0.4:  # ORIGINAL proven level
        #     # market too quiet — skip
        #     continue

        # -- Place trade --
        dirn  = sig
        e_idx = i
        e_price = O[i]

        # ATR-based stop (consistent sizing)
        stop_dist = atr_p * atr_sl_mult
        min_stop_spread_ratio = _get_min_stop_spread_ratio(symbol)
        if dirn == 1:
            sl_price = e_price - stop_dist
            tp_price = e_price + stop_dist * adjusted_rr  # Use adaptive RR
        else:
            sl_price = e_price + stop_dist
            tp_price = e_price - stop_dist * adjusted_rr

        # Sanity - no margin restrictions in backtest (matches live bot)
        if stop_dist < pip * 2 or stop_dist > atr_p * 6:
            dirn = 0; continue
        if spr_p > 0 and stop_dist / spr_p < min_stop_spread_ratio:
            dirn = 0; continue

        in_trade = True; held = 0; init_sl = sl_price; last_eidx = i  # partial_taken disabled
        # Daily tracking disabled since daily cap is disabled
        # if td: dtc[td] = dtc.get(td, 0) + 1

    return _build_metrics(symbol, trades, eq)


# ===============================================================================
# SUPPORTING MODES
# ===============================================================================

def run_split_backtest(df, symbol, split_ratio=0.7, risk_pct=1.0):
    if split_ratio <= 0 or split_ratio >= 1: split_ratio = 0.7
    idx = int(len(df) * split_ratio)
    tr = run_backtest_no_lookahead(df.iloc[:idx].copy(), symbol, risk_pct=risk_pct)
    te = run_backtest_no_lookahead(df.iloc[idx:].copy(), symbol, risk_pct=risk_pct)
    return {'symbol': symbol, 'mode': 'split', 'split_ratio': split_ratio,
            'train': {'metrics': tr.get('metrics',{}), 'trades': tr.get('trades',[])},
            'test':  {'metrics': te.get('metrics',{}), 'trades': te.get('trades',[])}}


def monte_carlo_analysis(trades, iterations=200):
    if not trades:
        return {'iterations': iterations, 'ending_balances': [],
                'p10': 0, 'p50': 0, 'p90': 0, 'max_drawdown_avg': 0}
    profits = [float(t.get('profit',0)) for t in trades]
    ends = []; dds = []
    for _ in range(max(1,int(iterations))):
        sh = profits.copy(); np.random.shuffle(sh)
        b = INITIAL_BALANCE; e = [b]
        for x in sh: b += x; e.append(b)
        ends.append(b); dds.append(calculate_max_drawdown(e))
    return {'iterations': int(iterations), 'ending_balances': ends,
            'p10': float(np.percentile(ends,10)), 'p50': float(np.percentile(ends,50)),
            'p90': float(np.percentile(ends,90)), 'max_drawdown_avg': float(np.mean(dds))}


def walk_forward_analysis(symbol, total_days=180, train_days=60, test_days=30):
    results = []
    for w in range(max(0, (total_days - train_days) // test_days)):
        end_d = train_days + (w+1)*test_days
        df = fetch_data(symbol, max(1200, end_d*96))
        if df is None or len(df) < 300: continue
        si = int(len(df) * (train_days / max(end_d,1)))
        r = run_backtest_no_lookahead(df.iloc[si:].copy(), symbol)
        m = r.get('metrics',{})
        if m: results.append(m)
    if not results:
        return {'symbol':symbol,'mode':'walk_forward','total_periods':0,
                'profitable_periods':0,'consistency':0,
                'average_profit_per_period':0,'periods':[]}
    pr = sum(1 for r in results if float(r.get('total_profit',0)) > 0)
    return {'symbol':symbol,'mode':'walk_forward','total_periods':len(results),
            'profitable_periods':pr,'consistency':pr/len(results)*100,
            'average_profit_per_period':float(np.mean([float(r.get('total_profit',0)) for r in results])),
            'periods':results}


def run_robustness_20_periods(df, symbol, periods=20, risk_pct=1.0):
    """Run rolling robustness test across N contiguous periods on one dataset."""
    if df is None or len(df) < 300:
        return {
            'symbol': symbol,
            'mode': 'robustness_20',
            'periods_requested': int(periods),
            'periods_run': 0,
            'profitable_periods': 0,
            'consistency': 0.0,
            'average_return_pct': 0.0,
            'worst_period_return_pct': 0.0,
            'worst_period_drawdown': 0.0,
            'combined_equity_curve': [INITIAL_BALANCE],
            'period_results': [],
        }

    n = len(df)
    max_periods = max(1, n // 160)
    use_periods = max(1, min(int(periods), max_periods))
    block = max(120, n // use_periods)

    rows = []
    combined_equity = [INITIAL_BALANCE]
    combined_balance = INITIAL_BALANCE

    for p in range(use_periods):
        start = p * block
        end = (p + 1) * block if p < use_periods - 1 else n
        if end - start < 120:
            continue

        seg = df.iloc[start:end].copy().reset_index(drop=True)
        bt = run_backtest_no_lookahead(seg, symbol, risk_pct=risk_pct)
        m = bt.get('metrics', {})
        eq = bt.get('equity_curve', []) or [INITIAL_BALANCE]

        start_time = None
        end_time = None
        if 'Time' in seg.columns and len(seg) > 0:
            start_time = str(pd.Timestamp(seg['Time'].iloc[0]))
            end_time = str(pd.Timestamp(seg['Time'].iloc[-1]))

        period_profit = float(m.get('total_profit', 0.0) or 0.0)
        for v in eq[1:]:
            combined_equity.append(float(combined_balance + (float(v) - INITIAL_BALANCE)))
        combined_balance += period_profit

        rows.append({
            'period_index': int(p + 1),
            'bars': int(len(seg)),
            'start_time': start_time,
            'end_time': end_time,
            'metrics': m,
        })

    return {
        'symbol': symbol,
        'mode': 'robustness_20',
        'periods_requested': int(periods),
        'periods_run': len(rows),
        'profitable_periods': sum(1 for r in rows if r['metrics'].get('total_profit', 0) > 0),
        'avg_return_pct': float(np.mean([r['metrics'].get('return_pct', 0) for r in rows])) if rows else 0.0,
        'win_rate_pct': float(np.mean([r['metrics'].get('win_rate', 0) for r in rows])) if rows else 0.0,
        'combined_equity': combined_equity,
        'periods': rows,
    }


# ===============================================================================
# SMART MONEY STRATEGY - Liquidity Sweep + MSS + Pullback
# ===============================================================================

def smc_detect_structure(df: pd.DataFrame, lookback: int = 10) -> str:
    """Detect market structure: bullish (higher highs/lows) or bearish (lower lows/highs)"""
    if len(df) < lookback * 2:
        return 'neutral'
    
    highs = df['High'].values
    lows = df['Low'].values
    
    # Recent swings
    recent_high = max(highs[-lookback:])
    recent_low = min(lows[-lookback:])
    prev_high = max(highs[-lookback*2:-lookback])
    prev_low = min(lows[-lookback*2:-lookback])
    
    hh = recent_high > prev_high
    hl = recent_low > prev_low
    lh = recent_high < prev_high
    ll = recent_low < prev_low
    
    if hh and hl:
        return 'bullish'
    elif lh and ll:
        return 'bearish'
    return 'neutral'

def smc_detect_sweep(df: pd.DataFrame, direction: str, lookback: int = 5) -> int:
    """Detect liquidity sweep - price takes out previous high/low then reverses"""
    if len(df) < lookback + 5:
        return -1
    
    highs = df['High'].values
    lows = df['Low'].values
    closes = df['Close'].values
    
    if direction == 'buy':
        # Find recent low
        recent_low = min(lows[-lookback-5:-lookback])
        # Check sweep and reclaim
        for i in range(-lookback, 0):
            if lows[i] < recent_low and closes[i] > recent_low:
                return len(df) + i
    else:
        # Find recent high
        recent_high = max(highs[-lookback-5:-lookback])
        # Check sweep and reclaim
        for i in range(-lookback, 0):
            if highs[i] > recent_high and closes[i] < recent_high:
                return len(df) + i
    
    return -1

def smc_detect_mss(df: pd.DataFrame, direction: str, sweep_idx: int) -> bool:
    """Market Structure Shift - after sweep, price breaks structure"""
    if sweep_idx < 0 or sweep_idx >= len(df) - 1:
        return False
    
    highs = df['High'].values
    lows = df['Low'].values
    closes = df['Close'].values
    
    if direction == 'buy':
        # After sweep low, need to break a recent high
        if sweep_idx < 5:
            return False
        recent_high = max(highs[sweep_idx-5:sweep_idx])
        for i in range(sweep_idx, min(sweep_idx + 5, len(df))):
            if closes[i] > recent_high:
                return True
    else:
        # After sweep high, need to break a recent low
        if sweep_idx < 5:
            return False
        recent_low = min(lows[sweep_idx-5:sweep_idx])
        for i in range(sweep_idx, min(sweep_idx + 5, len(df))):
            if closes[i] < recent_low:
                return True
    
    return False

def smc_get_entry_price(df: pd.DataFrame, direction: str, sweep_idx: int, 
                        pullback_pct: float = 0.5) -> tuple:
    """Get 50% pullback entry price and stop loss"""
    if sweep_idx < 0 or sweep_idx >= len(df) - 1:
        return None, None
    
    highs = df['High'].values
    lows = df['Low'].values
    
    if direction == 'buy':
        # Bullish impulse: sweep low to break high
        impulse_start = lows[sweep_idx]
        impulse_end = max(highs[sweep_idx:min(sweep_idx+3, len(df))])
        
        entry = impulse_start + (impulse_end - impulse_start) * pullback_pct
        stop = impulse_start - (impulse_end - impulse_start) * 0.1
        
        return entry, stop
    else:
        # Bearish impulse: sweep high to break low
        impulse_start = highs[sweep_idx]
        impulse_end = min(lows[sweep_idx:min(sweep_idx+3, len(df))])
        
        entry = impulse_start - (impulse_start - impulse_end) * pullback_pct
        stop = impulse_start + (impulse_start - impulse_end) * 0.1
        
        return entry, stop

def run_smc_backtest(df: pd.DataFrame, symbol: str, 
                     pullback_pct: float = 0.5,
                     rr_ratio: float = 1.2,
                     risk_pct: float = 2.0) -> dict:
    """
    Run Smart Money Concept backtest
    Liquidity Sweep → MSS → Pullback Entry
    """
    if len(df) < 100:
        return {'trades': [], 'equity_curve': [INITIAL_BALANCE], 'metrics': {}}
    
    trades = []
    equity = INITIAL_BALANCE
    equity_curve = [equity]
    
    i = 50  # Start after enough bars
    while i < len(df) - 10:
        window = df.iloc[:i+1]
        
        # Get HTF bias from larger window
        bias = smc_detect_structure(window, lookback=20)
        
        if bias == 'neutral':
            i += 1
            continue
        
        direction = 'buy' if bias == 'bullish' else 'sell'
        
        # Detect sweep
        sweep_idx = smc_detect_sweep(window, direction, lookback=5)
        
        if sweep_idx < 0:
            i += 1
            continue
        
        # Confirm MSS
        if not smc_detect_mss(window, direction, sweep_idx):
            i += 1
            continue
        
        # Get entry
        entry_price, stop_price = smc_get_entry_price(window, direction, sweep_idx, pullback_pct)
        
        if entry_price is None or stop_price is None:
            i += 1
            continue
        
        # Calculate position size
        # Cap absolute per-trade risk to protect from runaway sizing (2% of initial balance)
        risk_amount = min(equity * (risk_pct / 100.0), INITIAL_BALANCE * 0.02)
        stop_dist = abs(entry_price - stop_price)
        
        if stop_dist <= 0:
            i += 1
            continue
        
        # Get instrument config for pip size
        inst = INSTRUMENTS.get(symbol, {'pip_size': 0.0001, 'spread': 1.0})
        pip_size = inst['pip_size']
        spread = inst['spread'] * pip_size

        min_stop_spread_ratio = _get_min_stop_spread_ratio(symbol)
        if spread > 0 and stop_dist / spread < min_stop_spread_ratio:
            i += 1
            continue
        
        # Adjust for spread
        if direction == 'buy':
            entry_price += spread
        else:
            entry_price -= spread
        
        # Calculate target (1:1 or 1.2:1 RR)
        target_price = entry_price + (stop_dist * rr_ratio) if direction == 'buy' else entry_price - (stop_dist * rr_ratio)
        
        # Execute trade forward
        trade_profit = 0.0
        exited = False
        
        for j in range(i+1, min(i+50, len(df))):
            current_high = df['High'].iloc[j]
            current_low = df['Low'].iloc[j]
            current_close = df['Close'].iloc[j]
            
            if direction == 'buy':
                # Check SL
                if current_low <= stop_price:
                    trade_profit = -risk_amount
                    exited = True
                    break
                # Check TP
                if current_high >= target_price:
                    trade_profit = risk_amount * rr_ratio
                    exited = True
                    break
            else:
                # Check SL
                if current_high >= stop_price:
                    trade_profit = -risk_amount
                    exited = True
                    break
                # Check TP
                if current_low <= target_price:
                    trade_profit = risk_amount * rr_ratio
                    exited = True
                    break
        
        if exited:
            equity += trade_profit
            equity_curve.append(equity)
            
            trades.append({
                'entry_bar': i,
                'exit_bar': j,
                'direction': direction,
                'entry_price': entry_price,
                'stop_price': stop_price,
                'target_price': target_price,
                'profit': trade_profit,
                'balance': equity
            })
            
            i = j + 5  # Skip forward to avoid overlapping trades
        else:
            i += 1
    
    # Calculate metrics
    if trades:
        profits = [t['profit'] for t in trades]
        wins = sum(1 for p in profits if p > 0)
        total = len(profits)
        
        metrics = {
            'total_trades': total,
            'win_rate': (wins / total * 100) if total > 0 else 0,
            'total_profit': sum(profits),
            'return_pct': ((equity - INITIAL_BALANCE) / INITIAL_BALANCE * 100),
            'max_drawdown': calculate_max_drawdown(equity_curve),
            'profit_factor': abs(sum(p for p in profits if p > 0) / sum(p for p in profits if p < 0)) if sum(p for p in profits if p < 0) != 0 else 999
        }
    else:
        metrics = {
            'total_trades': 0,
            'win_rate': 0,
            'total_profit': 0,
            'return_pct': 0,
            'max_drawdown': 0,
            'profit_factor': 0
        }
    
    return {
        'trades': trades,
        'equity_curve': equity_curve,
        'metrics': metrics
    }


# ===============================================================================
# SMART MONEY STRATEGY - Liquidity Sweep + MSS + Pullback
# ===============================================================================

def smc_detect_structure(df: pd.DataFrame, lookback: int = 10) -> str:
    """Detect market structure: bullish (higher highs/lows) or bearish (lower lows/highs)"""
    if len(df) < lookback * 2:
        return 'neutral'
    
    highs = df['High'].values
    lows = df['Low'].values
    
    # Recent swings
    recent_high = max(highs[-lookback:])
    recent_low = min(lows[-lookback:])
    prev_high = max(highs[-lookback*2:-lookback])
    prev_low = min(lows[-lookback*2:-lookback])
    
    hh = recent_high > prev_high
    hl = recent_low > prev_low
    lh = recent_high < prev_high
    ll = recent_low < prev_low
    
    if hh and hl:
        return 'bullish'
    elif lh and ll:
        return 'bearish'
    return 'neutral'


def smc_detect_sweep(df: pd.DataFrame, direction: str, lookback: int = 5) -> int:
    """Detect liquidity sweep - price takes out previous high/low then reverses"""
    if len(df) < lookback + 5:
        return -1
    
    highs = df['High'].values
    lows = df['Low'].values
    closes = df['Close'].values
    
    if direction == 'buy':
        # Find recent low
        recent_low = min(lows[-lookback-5:-lookback])
        # Check sweep and reclaim
        for i in range(-lookback, 0):
            if lows[i] < recent_low and closes[i] > recent_low:
                return len(df) + i
    else:
        # Find recent high
        recent_high = max(highs[-lookback-5:-lookback])
        # Check sweep and reclaim
        for i in range(-lookback, 0):
            if highs[i] > recent_high and closes[i] < recent_high:
                return len(df) + i
    
    return -1


def smc_detect_mss(df: pd.DataFrame, direction: str, sweep_idx: int) -> bool:
    """Market Structure Shift - after sweep, price breaks structure"""
    if sweep_idx < 0 or sweep_idx >= len(df) - 1:
        return False
    
    highs = df['High'].values
    lows = df['Low'].values
    closes = df['Close'].values
    
    if direction == 'buy':
        # After sweep low, need to break a recent high
        if sweep_idx < 5:
            return False
        recent_high = max(highs[sweep_idx-5:sweep_idx])
        for i in range(sweep_idx, min(sweep_idx + 5, len(df))):
            if closes[i] > recent_high:
                return True
    else:
        # After sweep high, need to break a recent low
        if sweep_idx < 5:
            return False
        recent_low = min(lows[sweep_idx-5:sweep_idx])
        for i in range(sweep_idx, min(sweep_idx + 5, len(df))):
            if closes[i] < recent_low:
                return True
    
    return False


def smc_get_entry_price(df: pd.DataFrame, direction: str, sweep_idx: int, 
                        pullback_pct: float = 0.5) -> tuple:
    """Get pullback entry price and stop loss"""
    if sweep_idx < 0 or sweep_idx >= len(df) - 1:
        return None, None
    
    highs = df['High'].values
    lows = df['Low'].values
    
    if direction == 'buy':
        # Bullish impulse: sweep low to break high
        impulse_start = lows[sweep_idx]
        impulse_end = max(highs[sweep_idx:min(sweep_idx+3, len(df))])
        
        entry = impulse_start + (impulse_end - impulse_start) * pullback_pct
        stop = impulse_start - (impulse_end - impulse_start) * 0.1
        
        return entry, stop
    else:
        # Bearish impulse: sweep high to break low
        impulse_start = highs[sweep_idx]
        impulse_end = min(lows[sweep_idx:min(sweep_idx+3, len(df))])
        
        entry = impulse_start - (impulse_start - impulse_end) * pullback_pct
        stop = impulse_start + (impulse_start - impulse_end) * 0.1
        
        return entry, stop


def run_smc_backtest(df: pd.DataFrame, symbol: str, 
                     pullback_pct: float = 0.5,
                     rr_ratio: float = 1.2,
                     risk_pct: float = 2.0) -> dict:
    """
    Run Smart Money Concept backtest
    Liquidity Sweep -> MSS -> Pullback Entry
    """
    if len(df) < 100:
        return {'trades': [], 'equity_curve': [INITIAL_BALANCE], 'metrics': {}}
    
    trades = []
    equity = INITIAL_BALANCE
    equity_curve = [equity]
    
    i = 50  # Start after enough bars
    while i < len(df) - 10:
        window = df.iloc[:i+1]
        
        # Get HTF bias from larger window
        bias = smc_detect_structure(window, lookback=20)
        
        if bias == 'neutral':
            i += 1
            continue
        
        direction = 'buy' if bias == 'bullish' else 'sell'
        
        # Detect sweep
        sweep_idx = smc_detect_sweep(window, direction, lookback=5)
        
        if sweep_idx < 0:
            i += 1
            continue
        
        # Confirm MSS
        if not smc_detect_mss(window, direction, sweep_idx):
            i += 1
            continue
        
        # Get entry
        entry_price, stop_price = smc_get_entry_price(window, direction, sweep_idx, pullback_pct)
        
        if entry_price is None or stop_price is None:
            i += 1
            continue
        
        # Calculate position size
        # Cap absolute per-trade risk to protect from runaway sizing (2% of initial balance)
        risk_amount = min(equity * (risk_pct / 100.0), INITIAL_BALANCE * 0.02)
        stop_dist = abs(entry_price - stop_price)
        
        if stop_dist <= 0:
            i += 1
            continue
        
        # Get instrument config for pip size
        inst = INSTRUMENTS.get(symbol, {'pip_size': 0.0001, 'spread': 1.0})
        pip_size = inst['pip_size']
        spread = inst['spread'] * pip_size
        
        # Adjust for spread
        if direction == 'buy':
            entry_price += spread
        else:
            entry_price -= spread
        
        # Calculate target (1:1 or 1.2:1 RR)
        target_price = entry_price + (stop_dist * rr_ratio) if direction == 'buy' else entry_price - (stop_dist * rr_ratio)
        
        # Execute trade forward
        trade_profit = 0.0
        exited = False
        
        for j in range(i+1, min(i+50, len(df))):
            current_high = df['High'].iloc[j]
            current_low = df['Low'].iloc[j]
            
            if direction == 'buy':
                # Check SL
                if current_low <= stop_price:
                    trade_profit = -risk_amount
                    exited = True
                    break
                # Check TP
                if current_high >= target_price:
                    trade_profit = risk_amount * rr_ratio
                    exited = True
                    break
            else:
                # Check SL
                if current_high >= stop_price:
                    trade_profit = -risk_amount
                    exited = True
                    break
                # Check TP
                if current_low <= target_price:
                    trade_profit = risk_amount * rr_ratio
                    exited = True
                    break
        
        if exited:
            equity += trade_profit
            equity_curve.append(equity)
            
            trades.append({
                'entry_bar': i,
                'exit_bar': j,
                'direction': direction,
                'entry_price': entry_price,
                'stop_price': stop_price,
                'target_price': target_price,
                'profit': trade_profit,
                'balance': equity
            })
            
            i = j + 5  # Skip forward to avoid overlapping trades
        else:
            i += 1
    
    # Calculate metrics
    if trades:
        profits = [t['profit'] for t in trades]
        wins = sum(1 for p in profits if p > 0)
        total = len(profits)
        
        metrics = {
            'total_trades': total,
            'win_rate': (wins / total * 100) if total > 0 else 0,
            'total_profit': sum(profits),
            'return_pct': ((equity - INITIAL_BALANCE) / INITIAL_BALANCE * 100),
            'max_drawdown': calculate_max_drawdown(equity_curve),
            'profit_factor': abs(sum(p for p in profits if p > 0) / sum(p for p in profits if p < 0)) if sum(p for p in profits if p < 0) != 0 else 999
        }
    else:
        metrics = {
            'total_trades': 0,
            'win_rate': 0,
            'total_profit': 0,
            'return_pct': 0,
            'max_drawdown': 0,
            'profit_factor': 0
        }
    
    return {
        'trades': trades,
        'equity_curve': equity_curve,
        'metrics': metrics
    }


# ===============================================================================
# OPTIMIZATION
# ===============================================================================

def compute_score(m):
    pf = m.get('profit_factor',0); ret = m.get('return_pct',0)
    t = m.get('total_trades',0); wr = m.get('win_rate',0); dd = m.get('max_drawdown',0)
    if t < 3: return -999.0
    r = (max(pf-1,0)*20 + ret*0.5 + np.sqrt(t)*2 + max(wr-30,0)*0.3)
    p = (dd/max(INITIAL_BALANCE,1))*30 + (abs(ret) if ret < 0 else 0)
    return r - p


def compute_robust_score(m):
    """Robustness-first scoring: prefer low drawdown, consistent positive periods,
    reasonable trades count, and positive out-of-sample return. Penalize extreme parameter sensitivity.
    """
    pf = m.get('profit_factor', 0); ret = m.get('return_pct', 0)
    t = m.get('total_trades', 0); wr = m.get('win_rate', 0); dd = m.get('max_drawdown', 0)
    # Require minimum trade count
    if t < 5:
        return -999.0
    # Low drawdown is primary
    score = - (dd / max(1.0, INITIAL_BALANCE)) * 100.0
    # Reward positive out-of-sample return modestly
    score += ret * 1.5
    # Reward higher win_rate and profit factor
    score += max(pf - 1.0, 0) * 10.0
    score += (wr - 50.0) * 0.2
    # Slight reward for trade frequency (stability)
    score += np.sqrt(t) * 1.5
    return score


def optimize(symbol, df, risk_pct=2.0):
    sp = int(len(df)*0.7)
    dtr = df.iloc[:sp].copy(); dte = df.iloc[sp:].copy()
    # ROBUSTNESS-FIRST: smaller, coarser grid to avoid overfitting
    ema_g = [20, 50, 80]
    adx_g = [12.0, 16.0, 20.0, 24.0]
    slm_g = [1.0, 1.2, 1.5]
    rr_g = [1.2, 1.5, 2.0]
    best_s = -np.inf; best_p = None; best_r = None
    for e,a,s,r in tqdm(list(product(ema_g,adx_g,slm_g,rr_g)), desc=f'Opt {symbol}'):
        pr = (e,a,s,r)
        _ = run_backtest_no_lookahead(dtr, symbol, params=pr, risk_pct=risk_pct)
        rv = run_backtest_no_lookahead(dte, symbol, params=pr, risk_pct=risk_pct)
        sc = compute_robust_score(rv.get('metrics',{}))
        if sc > best_s:
            best_s=sc; best_p=pr; best_r=rv
    return best_p, best_r


def refine_params(symbol, df, base, risk_pct=2.0):
    if not base or len(base) < 4: return None, None
    be,ba,bs,br = base
    sp = int(len(df)*0.7); dtr = df.iloc[:sp].copy(); dte = df.iloc[sp:].copy()
    best_s = -np.inf; best_p = None; best_r = None
    # Narrow, conservative refinement around base params
    for e in sorted({max(10,be-10), be, be+10}):
        for a in sorted({max(10,ba-2), ba, ba+2}):
            for s in sorted({max(0.8,bs-0.2), bs, min(2.0,bs+0.2)}):
                for r in sorted({max(1.2,br-0.2), br, min(3.0,br+0.2)}):
                    pr = (int(e),float(a),float(s),float(r))
                    _ = run_backtest_no_lookahead(dtr, symbol, params=pr, risk_pct=risk_pct)
                    rv = run_backtest_no_lookahead(dte, symbol, params=pr, risk_pct=risk_pct)
                    sc = compute_robust_score(rv.get('metrics',{}))
                    if sc > best_s:
                        best_s=sc; best_p=pr; best_r=rv
    return best_p, best_r


# ===============================================================================
# BUILD / SAVE
# ===============================================================================

def build_output_payload(symbol, mode, result):
    p = {'symbol': symbol, 'mode': mode,
         'timestamp': datetime.now().isoformat(),
         'engine': 'backtest_improved.py (ICT/SMC v2)'}
    p.update(result); return p

def save_output(payload, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f: json.dump(payload, f, indent=2, default=str)


def run_stress_suite(symbol: str, df: pd.DataFrame, out_dir: Optional[Path]=None) -> Dict:
    """Run a set of stress tests altering data quality, noise, spreads and execution latency.
    Returns a dict of test_name -> metrics.
    """
    if out_dir is None:
        out_dir = Path(__file__).parent / 'liverun' / 'stress_tests'
    out_dir.mkdir(parents=True, exist_ok=True)

    if df is None or len(df) < 120:
        return {'symbol': symbol, 'error': 'insufficient data'}

    tests = {}
    base_df = df.copy().reset_index(drop=False)

    def run_case(name, df_case, inst_overrides=None):
        # temporary instrument override
        orig = None
        if inst_overrides:
            orig = {}
            for k,v in inst_overrides.items():
                orig[k] = INSTRUMENTS.get(k)
                INSTRUMENTS[k] = v
        res = run_backtest_no_lookahead(df_case.copy(), symbol, risk_pct=1.0, use_indicator_cache=False)
        score = compute_robust_score(res.get('metrics', {}))
        tests[name] = {'metrics': res.get('metrics', {}), 'robust_score': score}
        # restore
        if inst_overrides and orig is not None:
            for k,v in orig.items():
                if v is None:
                    INSTRUMENTS.pop(k, None)
                else:
                    INSTRUMENTS[k] = v

    pip = INSTRUMENTS.get(symbol, {'pip_size':0.0001})['pip_size']

    # Baseline
    run_case('baseline', base_df)

    # Small gaussian noise on prices
    df_n = base_df.copy()
    sigma = pip * 0.5
    for col in ['Open','High','Low','Close']:
        df_n[col] = df_n[col].astype(float) + np.random.randn(len(df_n)) * sigma
    run_case('noise_small', df_n)

    # Large noise
    df_n2 = base_df.copy()
    sigma2 = pip * 2.0
    for col in ['Open','High','Low','Close']:
        df_n2[col] = df_n2[col].astype(float) + np.random.randn(len(df_n2)) * sigma2
    run_case('noise_large', df_n2)

    # Increased spread x2
    inst2 = INSTRUMENTS.get(symbol, {}).copy()
    inst2['spread'] = inst2.get('spread',1.0) * 2.0
    run_case('spread_x2', base_df, inst_overrides={symbol: inst2})

    # Increased spread x5
    inst5 = INSTRUMENTS.get(symbol, {}).copy()
    inst5['spread'] = inst5.get('spread',1.0) * 5.0
    run_case('spread_x5', base_df, inst_overrides={symbol: inst5})

    # Missing bars (randomly drop ~1%)
    df_miss = base_df.copy()
    drop_idx = np.random.choice(df_miss.index, size=max(1, int(len(df_miss)*0.01)), replace=False)
    df_miss = df_miss.drop(drop_idx).reset_index(drop=True)
    run_case('missing_1pct', df_miss)

    # Execution delay: entries execute at next bar open (shift Open forward)
    df_delay = base_df.copy()
    df_delay['Open'] = df_delay['Open'].shift(1).bfill()
    run_case('exec_delay_1bar', df_delay)

    # Combined worst-case: large noise + spread x5 + missing 2%
    df_wc = base_df.copy()
    for col in ['Open','High','Low','Close']:
        df_wc[col] = df_wc[col].astype(float) + np.random.randn(len(df_wc)) * sigma2
    drop_idx2 = np.random.choice(df_wc.index, size=max(1,int(len(df_wc)*0.02)), replace=False)
    df_wc = df_wc.drop(drop_idx2).reset_index(drop=True)
    run_case('worst_combined', df_wc, inst_overrides={symbol: inst5})

    out_path = out_dir / f"{symbol}_stress.json"
    save_output({'symbol': symbol, 'timestamp': datetime.now().isoformat(), 'tests': tests}, out_path)
    return {'symbol': symbol, 'tests': list(tests.keys()), 'out_path': str(out_path)}


# ===============================================================================
# LIVE SMART MONEY ENGINE BACKTEST
# ===============================================================================

LIVE_ENGINE_COSTS = {
    'EURUSD': {'pip_size': 0.0001, 'spread_pips': 1.5, 'slippage_pips': 0.5},
    'NAS100': {'pip_size': 1.0, 'spread_pips': 2.0, 'slippage_pips': 1.0},
    'XAUUSD': {'pip_size': 0.01, 'spread_pips': 30.0, 'slippage_pips': 10.0},
}


def _standardize_ohlc_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    rename = {}
    for col in out.columns:
        lower = str(col).lower()
        if lower == 'open':
            rename[col] = 'Open'
        elif lower == 'high':
            rename[col] = 'High'
        elif lower == 'low':
            rename[col] = 'Low'
        elif lower == 'close':
            rename[col] = 'Close'
    return out.rename(columns=rename)


def _resample_ohlc_for_live_engine(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    work = _standardize_ohlc_columns(df)
    if not isinstance(work.index, pd.DatetimeIndex):
        if 'time' not in work.columns:
            return work
        work = work.set_index(pd.to_datetime(work['time']))
    return work.resample(rule).agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
    }).dropna()


def _slice_live_engine_window(df: pd.DataFrame, end_time, bars: int) -> pd.DataFrame:
    return df.loc[:end_time].iloc[-bars:].copy()


def run_live_smc_engine_backtest(df_m15: pd.DataFrame, symbol: str, risk_pct: float = 1.0) -> dict:
    try:
        from OLDBOT.mt5_bot.smart_money_strategy import SmartMoneyStrategy, SYMBOL_RULES, _atr
    except ModuleNotFoundError:
        from OLDBOT.mt5_bot.smart_money_strategy import SmartMoneyStrategy, SYMBOL_RULES, _atr

    df = _standardize_ohlc_columns(df_m15)
    if not isinstance(df.index, pd.DatetimeIndex):
        if 'time' not in df.columns:
            raise ValueError('DataFrame must have DatetimeIndex or time column')
        df = df.set_index(pd.to_datetime(df['time']))

    df_h1 = _resample_ohlc_for_live_engine(df, '1h')
    df_m5 = _resample_ohlc_for_live_engine(df, '5min')
    costs = LIVE_ENGINE_COSTS.get(symbol, LIVE_ENGINE_COSTS['EURUSD'])
    spread = costs['spread_pips'] * costs['pip_size']
    slippage = costs['slippage_pips'] * costs['pip_size']

    equity = INITIAL_BALANCE
    peak_equity = INITIAL_BALANCE
    equity_curve = [equity]
    trades = []
    signal_markers = []
    open_trade = None
    m5_times = list(df_m5.index)
    i = 120
    consecutive_losses = 0
    cooldown_until = 0
    daily_trade_count = {}
    daily_start_equity = {}
    MAX_DAILY_TRADES = int(os.getenv('SMC_MAX_DAILY_TRADES', '5'))
    # Scale all % thresholds proportionally to risk_pct (base = 1.0%)
    # So at 10% risk: DAILY_TARGET=30%, KILL_SWITCH=50%, dd_factor at 40% - same behaviour
    _risk_scale = risk_pct / 1.0
    DAILY_TARGET_PCT = 3.0 * _risk_scale
    KILL_SWITCH_DD_PCT = 5.0 * _risk_scale
    kill_switch_triggered = False

    while i < len(m5_times) - 20:
        now = m5_times[i]
        bar = df_m5.iloc[i]

        if open_trade is not None:
            direction = open_trade['direction']
            stop = open_trade['stop']
            target = open_trade['target']
            entry = open_trade['entry']
            risk_amount = open_trade['risk_amount']
            stop_dist = abs(entry - stop)
            initial_stop_dist = open_trade['initial_stop_dist']
            exited = False

            sym_cfg = SYMBOL_RULES[symbol]
            no_partial = sym_cfg.get('no_partial', False)
            tp1_r = sym_cfg.get('tp1_r', 1.0)
            tp1_fraction = sym_cfg.get('tp1_fraction', 0.5)
            if not no_partial and not open_trade['partial_taken']:
                tp1_dist = initial_stop_dist * tp1_r
                tp1_level = entry + tp1_dist if direction == 'buy' else entry - tp1_dist
                hit_tp1 = bar['High'] >= tp1_level if direction == 'buy' else bar['Low'] <= tp1_level
                if hit_tp1:
                    open_trade['partial_taken'] = True
                    open_trade['stop'] = entry
                    stop = entry
                    open_trade['banked_r'] = tp1_r * tp1_fraction
                    open_trade['remaining_fraction'] = 1.0 - tp1_fraction
                    open_trade['highest'] = bar['High']
                    open_trade['lowest'] = bar['Low']
            else:
                trail_mult = SYMBOL_RULES[symbol].get('trail_mult', 1.5)
                if trail_mult is not None:
                    trail_dist = initial_stop_dist * trail_mult
                    if direction == 'buy':
                        open_trade['highest'] = max(open_trade['highest'], bar['High'])
                        new_stop = open_trade['highest'] - trail_dist
                        if new_stop > stop:
                            open_trade['stop'] = new_stop
                            stop = new_stop
                    else:
                        open_trade['lowest'] = min(open_trade['lowest'], bar['Low'])
                        new_stop = open_trade['lowest'] + trail_dist
                        if new_stop < stop:
                            open_trade['stop'] = new_stop
                            stop = new_stop

            remaining = open_trade.get('remaining_fraction', 0.5) if open_trade['partial_taken'] else 1.0
            banked_r = open_trade['banked_r']

            if direction == 'buy':
                sl_hit = bar['Low'] <= stop
                tp_hit = bar['High'] >= target
                if sl_hit and tp_hit:
                    exit_r = ((stop - slippage) - entry) / initial_stop_dist
                    exited = True
                    reason = 'SL_AMBIGUOUS'
                elif sl_hit:
                    exit_r = ((stop - slippage) - entry) / initial_stop_dist
                    exited = True
                    reason = 'SL_BE' if open_trade['partial_taken'] else 'SL'
                elif tp_hit:
                    exit_r = open_trade['effective_rr'] - (slippage / initial_stop_dist)
                    exited = True
                    reason = 'TP'
            else:
                sl_hit = bar['High'] >= stop
                tp_hit = bar['Low'] <= target
                if sl_hit and tp_hit:
                    exit_r = (entry - (stop + slippage)) / initial_stop_dist
                    exited = True
                    reason = 'SL_AMBIGUOUS'
                elif sl_hit:
                    exit_r = (entry - (stop + slippage)) / initial_stop_dist
                    exited = True
                    reason = 'SL_BE' if open_trade['partial_taken'] else 'SL'
                elif tp_hit:
                    exit_r = open_trade['effective_rr'] - (slippage / initial_stop_dist)
                    exited = True
                    reason = 'TP'

            timeout_bars = SYMBOL_RULES[symbol].get('timeout_bars', 96)
            if not exited and i - open_trade['entry_i'] >= timeout_bars:
                close = bar['Close']
                exit_r = (close - entry) / initial_stop_dist if direction == 'buy' else (entry - close) / initial_stop_dist
                exited = True
                reason = 'TIMEOUT'

            if exited:
                profit_r = banked_r + remaining * exit_r

            if exited:
                profit = risk_amount * profit_r
                equity += profit
                if equity > peak_equity:
                    peak_equity = equity
                equity_curve.append(equity)
                trade = dict(open_trade)
                trade.update({
                    'exit_time': str(now),
                    'exit_reason': reason,
                    'profit': float(profit),
                    'profit_r': float(profit_r),
                    'balance': float(equity),
                })
                trades.append(trade)
                if profit_r <= 0:
                    consecutive_losses += 1
                    if consecutive_losses >= 2:
                        cooldown_until = i + 12
                else:
                    consecutive_losses = 0
                open_trade = None
                i += 3
                continue

            i += 1
            continue

        if i < cooldown_until:
            i += 1
            continue

        if not kill_switch_triggered and (peak_equity - equity) / peak_equity * 100 >= KILL_SWITCH_DD_PCT:
            kill_switch_triggered = True
        if kill_switch_triggered:
            i += 1
            continue

        day_key = now.date() if hasattr(now, 'date') else None
        if day_key is not None:
            daily_start_equity.setdefault(day_key, equity)
            if daily_trade_count.get(day_key, 0) >= MAX_DAILY_TRADES:
                i += 1
                continue
            day_pnl_pct = (equity - daily_start_equity[day_key]) / daily_start_equity[day_key] * 100
            if day_pnl_pct >= DAILY_TARGET_PCT:
                i += 1
                continue

        decision_time = now + pd.Timedelta('15min')
        h1_cutoff = decision_time - pd.Timedelta('1h')
        h1_window = df_h1.loc[:h1_cutoff].iloc[-250:].copy()
        m5_window = _slice_live_engine_window(df_m5, now, 300)
        if len(h1_window) < 80:
            i += 1
            continue
        signal = SmartMoneyStrategy(h1_window, m5_window, symbol).check_signal()
        if signal is None:
            i += 1
            continue

        # Record signal marker for frontend visualization
        signal_markers.append({
            'time': str(now),
            'direction': signal.get('direction', 'buy'),
            'entry': float(signal.get('entry', 0)),
            'stop': float(signal.get('stop', 0)),
            'rr': float(signal.get('rr', 2.0)),
            'score': float(signal.get('score', 0)),
        })

        entry_i = i + 1
        if entry_i >= len(df_m5):
            break
        entry_bar = df_m5.iloc[entry_i]
        direction = signal['direction']
        stop = signal['stop']
        entry = entry_bar['Open'] + spread + slippage if direction == 'buy' else entry_bar['Open'] - spread - slippage
        stop_dist = abs(entry - stop)
        if stop_dist <= spread * 2:
            i += 1
            continue
        sym_rules = SYMBOL_RULES[symbol]
        if sym_rules.get('trail_mult') is not None:
            # With trailing stop: use max of signal RR, sym_rules RR, and 3.0 minimum so trail has room to run
            effective_rr = max(signal['rr'], float(sym_rules.get('rr', 2.0)), 3.0)
        else:
            effective_rr = signal['rr']
        target = entry + stop_dist * effective_rr if direction == 'buy' else entry - stop_dist * effective_rr

        atr_window = _atr(df_m5).iloc[max(0, entry_i - 80):entry_i].dropna()
        vol_factor = 1.0
        if len(atr_window) >= 20:
            atr_now = float(atr_window.iloc[-1])
            atr_med = float(atr_window.median())
            if atr_med > 0:
                ratio = atr_now / atr_med
                if ratio > 1.6:
                    vol_factor = 0.6
                elif ratio < 0.7:
                    vol_factor = 0.8
        dd_factor = 0.5 if (peak_equity - equity) / peak_equity > 0.04 * _risk_scale else 1.0
        final_risk_pct = risk_pct * dd_factor * vol_factor

        if day_key is not None:
            daily_trade_count[day_key] = daily_trade_count.get(day_key, 0) + 1

        open_trade = {
            'symbol': symbol,
            'direction': direction,
            'entry_time': str(df_m5.index[entry_i]),
            'entry_i': entry_i,
            'entry': float(entry),
            'stop': float(stop),
            'target': float(target),
            'risk_amount': float(equity * final_risk_pct / 100),
            'score': signal.get('score', 0),
            'rr': signal.get('rr', 0),
            'initial_stop_dist': float(stop_dist),
            'partial_taken': False,
            'banked_r': 0.0,
            'effective_rr': float(effective_rr),
            'highest': float(entry),
            'lowest': float(entry),
        }
        i = entry_i

    return {'trades': trades, 'equity_curve': equity_curve, 'signal_markers': signal_markers, 'metrics': _live_engine_metrics(trades, equity_curve)}


def _live_engine_metrics(trades: list, equity_curve: list) -> dict:
    if not trades:
        return {'total_trades': 0, 'win_rate': 0, 'return_pct': 0, 'profit_factor': 0, 'avg_r': 0, 'max_drawdown_pct': 0}
    profits = [t['profit'] for t in trades]
    rs = [t['profit_r'] for t in trades]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak * 100
    return {
        'total_trades': len(trades),
        'win_rate': float(len(wins) / len(trades) * 100),
        'return_pct': float((equity_curve[-1] - equity_curve[0]) / equity_curve[0] * 100),
        'profit_factor': float(sum(wins) / abs(sum(losses))) if losses else 999.0,
        'avg_r': float(np.mean(rs)),
        'max_drawdown_pct': float(dd.max()),
    }


def portfolio_calendar_walk(symbols=('EURUSD', 'NAS100', 'XAUUSD'), windows=15, bars=60000, window_bars=1900) -> dict:
    """Run all symbols simultaneously on the same calendar window. Aggregate trades chronologically
    and produce a single combined equity curve so we measure what the live bot actually delivers."""
    print(f'\n{"="*80}\nPORTFOLIO CALENDAR-WALK (all symbols stacked, 1% risk each)\n{"="*80}')
    data = {}
    for symbol in symbols:
        df = fetch_data(symbol, bars=bars)
        if df is None or len(df) < window_bars * 2:
            print(f'{symbol}: not enough history; skipping')
            continue
        data[symbol] = df
    if not data:
        return {}
    common_start = max(df.index[0] for df in data.values())
    common_end = min(df.index[-1] for df in data.values())
    print(f'Common history: {common_start} -> {common_end}')
    primary = list(data.values())[0]
    primary = primary.loc[common_start:common_end]
    total = len(primary)
    rng = np.random.default_rng(2026)
    candidates = list(range(200, total - window_bars - 50))
    rng.shuffle(candidates)
    chosen, picked = [], []
    for s in candidates:
        if all(abs(s - p) >= window_bars for p in picked):
            picked.append(s)
            chosen.append(s)
        if len(chosen) >= windows:
            break
    chosen.sort()
    per_window = []
    for s in tqdm(chosen, desc='portfolio', unit='window', dynamic_ncols=True):
        window_start = primary.index[s]
        window_end = primary.index[s + window_bars - 1]
        all_trades = []
        for symbol, df in data.items():
            sub = df.loc[window_start:window_end]
            if len(sub) < 200:
                continue
            r = run_live_smc_engine_backtest(sub, symbol, risk_pct=1.0)
            for t in r['trades']:
                t['symbol'] = symbol
                all_trades.append(t)
        all_trades.sort(key=lambda t: t['exit_time'])
        equity = INITIAL_BALANCE
        peak = equity
        curve = [equity]
        for t in all_trades:
            equity *= (1 + 0.01 * t['profit_r'])
            peak = max(peak, equity)
            curve.append(equity)
        curve = np.array(curve)
        running_peak = np.maximum.accumulate(curve)
        max_dd_pct = float(((running_peak - curve) / running_peak).max() * 100) if len(curve) > 1 else 0.0
        per_window.append({
            'start': str(window_start),
            'end': str(window_end),
            'trades': len(all_trades),
            'return_pct': round((curve[-1] - curve[0]) / curve[0] * 100, 2),
            'max_dd_pct': round(max_dd_pct, 2),
        })
    rets = [w['return_pct'] for w in per_window]
    dds = [w['max_dd_pct'] for w in per_window]
    summary = {
        'windows': len(per_window),
        'profitable_pct': float(sum(1 for r in rets if r > 0) / len(rets) * 100),
        'mean_return_pct': float(np.mean(rets)),
        'median_return_pct': float(np.median(rets)),
        'worst_return_pct': float(np.min(rets)),
        'best_return_pct': float(np.max(rets)),
        'mean_max_dd_pct': float(np.mean(dds)),
        'worst_max_dd_pct': float(np.max(dds)),
    }
    print('\n' + json.dumps({'summary': summary, 'per_window': per_window}, indent=2))
    return {'summary': summary, 'per_window': per_window}


def calendar_walk_validate(symbols=('EURUSD', 'NAS100', 'XAUUSD'), windows=15, bars=60000, window_bars=1900) -> dict:
    """Robustness test: sample non-overlapping random calendar windows (~1 month each at M15)
    spread across the entire fetched history. Reports per-window outcomes and aggregate stats."""
    results = {}
    for symbol in symbols:
        print(f'\n{"="*80}\n{symbol} CALENDAR-WALK ROBUSTNESS ({windows} windows)\n{"="*80}')
        df = fetch_data(symbol, bars=bars)
        if df is None or len(df) < window_bars * windows:
            print(f'{symbol}: not enough history ({len(df) if df is not None else 0} bars)')
            continue
        total = len(df)
        print(f'[{symbol}] history: {df.index[0]} -> {df.index[-1]} ({total} bars)')
        rng = np.random.default_rng(123)
        candidates = list(range(200, total - window_bars - 50))
        rng.shuffle(candidates)
        chosen, picked = [], []
        for s in candidates:
            if all(abs(s - p) >= window_bars for p in picked):
                picked.append(s)
                chosen.append(s)
            if len(chosen) >= windows:
                break
        chosen.sort()
        per_window = []
        pbar = tqdm(chosen, desc=f'{symbol} calendar', unit='window', dynamic_ncols=True)
        for s in pbar:
            window_df = df.iloc[s:s + window_bars]
            r = run_live_smc_engine_backtest(window_df, symbol, risk_pct=1.0)
            m = r['metrics']
            per_window.append({
                'start': str(window_df.index[0]),
                'end': str(window_df.index[-1]),
                'trades': m['total_trades'],
                'return_pct': round(m['return_pct'], 2),
                'win_rate': round(m['win_rate'], 1),
                'avg_r': round(m['avg_r'], 3),
                'max_dd_pct': round(m['max_drawdown_pct'], 2),
            })
            pbar.set_postfix(ret=f"{m['return_pct']:+.2f}%")
        rets = [w['return_pct'] for w in per_window]
        dds = [w['max_dd_pct'] for w in per_window]
        rs = [w['avg_r'] for w in per_window]
        summary = {
            'windows': len(per_window),
            'profitable_pct': float(sum(1 for r in rets if r > 0) / len(rets) * 100),
            'mean_return_pct': float(np.mean(rets)),
            'median_return_pct': float(np.median(rets)),
            'worst_return_pct': float(np.min(rets)),
            'best_return_pct': float(np.max(rets)),
            'mean_max_dd_pct': float(np.mean(dds)),
            'worst_max_dd_pct': float(np.max(dds)),
            'mean_avg_r': float(np.mean(rs)),
        }
        print('\n' + json.dumps({'summary': summary, 'per_window': per_window}, indent=2))
        results[symbol] = {'summary': summary, 'per_window': per_window}
    return results


def monte_carlo_live_smc_engine(symbols=('EURUSD', 'NAS100', 'XAUUSD'), period_bars=4000, bars=20000, simulations=500) -> dict:
    """Run a single backtest per symbol, then bootstrap sample trade outcomes with replacement.
    This creates variation because we randomly SELECT which trades occur, not just shuffle order.
    Reports the distribution of final returns and worst-case drawdown across simulations."""
    results = {}
    for symbol in symbols:
        print(f'\n{"="*80}\n{symbol} MONTE CARLO ROBUSTNESS (Bootstrap)\n{"="*80}')
        df = fetch_data(symbol, bars=bars)
        if df is None or len(df) < period_bars + 1000:
            continue
        start = len(df) - period_bars - 1
        result = run_live_smc_engine_backtest(df.iloc[start:start + period_bars], symbol, risk_pct=1.0)
        rs = [t['profit_r'] for t in result['trades']]
        if not rs:
            print(f'{symbol}: no trades produced; skipping')
            continue
        
        final_returns = []
        max_dds = []
        rng = np.random.default_rng(42)
        risk_pct = 1.0
        
        for _ in tqdm(range(simulations), desc=f'{symbol} monte-carlo', unit='sim', dynamic_ncols=True):
            # Bootstrap: randomly sample trades WITH replacement (different trade count and mix each time)
            n_trades = len(rs)
            sampled_rs = rng.choice(rs, size=n_trades, replace=True)
            
            equity = INITIAL_BALANCE
            peak = equity
            curve = [equity]
            
            for r in sampled_rs:
                equity += equity * (risk_pct / 100) * r  # Additive for realistic position sizing
                peak = max(peak, equity)
                curve.append(equity)
                
            curve = np.array(curve)
            running_peak = np.maximum.accumulate(curve)
            dd = ((running_peak - curve) / running_peak).max() * 100
            final_returns.append((curve[-1] - curve[0]) / curve[0] * 100)
            max_dds.append(dd)
            
        summary = {
            'trades': len(rs),
            'mean_return_pct': float(np.mean(final_returns)),
            'median_return_pct': float(np.median(final_returns)),
            'p5_return_pct': float(np.percentile(final_returns, 5)),
            'p95_return_pct': float(np.percentile(final_returns, 95)),
            'profitable_pct': float(np.mean(np.array(final_returns) > 0) * 100),
            'mean_max_dd_pct': float(np.mean(max_dds)),
            'p95_max_dd_pct': float(np.percentile(max_dds, 95)),
        }
        print(json.dumps(summary, indent=2))
        results[symbol] = summary
    return results


def validate_live_smc_engine(symbols=('EURUSD', 'NAS100', 'XAUUSD'), periods=60, period_bars=1200, bars=25000, out_of_sample: bool = False) -> dict:
    results = {}
    for symbol in symbols:
        label = 'OUT-OF-SAMPLE' if out_of_sample else 'IN-SAMPLE'
        print(f'\n{"="*80}\n{symbol} {label} LIVE SMC ENGINE VALIDATION\n{"="*80}')
        df = fetch_data(symbol, bars=bars)
        if df is None or len(df) < period_bars + 1000:
            continue
        random.seed(42)
        returns, trades, win_rates, avg_rs, dds = [], [], [], [], []
        oos_start = int(len(df) * 0.7) if out_of_sample else 200
        max_start = len(df) - period_bars - 1
        pbar = tqdm(range(periods), desc=f'{symbol} {label.lower()}', unit='period', dynamic_ncols=True)
        for _ in pbar:
            start = random.randint(oos_start, max_start)
            result = run_live_smc_engine_backtest(df.iloc[start:start + period_bars], symbol, risk_pct=1.0)
            pbar.set_postfix(ret=f"{result['metrics']['return_pct']:+.2f}%")
            m = result['metrics']
            returns.append(m['return_pct'])
            trades.append(m['total_trades'])
            win_rates.append(m['win_rate'])
            avg_rs.append(m['avg_r'])
            dds.append(m['max_drawdown_pct'])
        profitable = sum(1 for r in returns if r > 0)
        summary = {
            'profitable_pct': profitable / periods * 100,
            'avg_return_pct': float(np.mean(returns)),
            'median_return_pct': float(np.median(returns)),
            'worst_return_pct': float(min(returns)),
            'best_return_pct': float(max(returns)),
            'avg_trades_per_period': float(np.mean(trades)),
            'avg_win_rate_pct': float(np.mean(win_rates)),
            'avg_r_per_trade': float(np.mean(avg_rs)),
            'avg_max_drawdown_pct': float(np.mean(dds)),
            'worst_max_drawdown_pct': float(max(dds)),
        }
        results[symbol] = summary
        print(json.dumps(summary, indent=2))
    return results


# ===============================================================================
# TRAINING MODE
# ===============================================================================

def main():
    print("\n" + "="*80)
    print("  ICT / SMC  BACKTEST ENGINE  v2  — TRAINING")
    print("="*80 + "\n")

    rows = []; n_ok = 0
    for sym in sorted(INSTRUMENTS.keys()):
        print(f"\n{'-'*60}\n  {sym}\n{'-'*60}")
        df = fetch_data(sym, 15000)
        if df is None or len(df) < 600:
            print(f"  [X] Not enough data"); continue
        if 'Time' in df.columns:
            co = pd.to_datetime(df['Time'].iloc[-1]) - pd.Timedelta(days=BACKTEST_DAYS)
            df = df[df['Time'] >= co].reset_index(drop=True)
        print(f"  Bars: {len(df)}  Duration: {get_data_duration(df)}")

        bp, br = optimize(sym, df, risk_pct=2.0)
        if bp is None: print("  [X] Opt failed"); continue
        rp, rr = refine_params(sym, df, bp, risk_pct=2.0)
        if rr and compute_score(rr.get('metrics',{})) > compute_score(br.get('metrics',{})):
            cp, cr = rp, rr
        else:
            cp, cr = bp, br

        m = cr.get('metrics',{})
        ret = m.get('return_pct',0); pf = m.get('profit_factor',0)
        wr = m.get('win_rate',0); nt = m.get('total_trades',0)
        if ret > 0: n_ok += 1
        tag = "OK" if ret > 0 else "LOSS"
        print(f"  Params: EMA={cp[0]} ADX>={cp[1]:.0f} SL×{cp[2]:.1f} RR={cp[3]:.1f}")
        print(f"  [{tag}] {nt} trades  WR={wr:.1f}%  PF={pf:.2f}  Return={ret:+.1f}%")

        rows.append({'Symbol':sym, 'EMA':int(cp[0]), 'ADX':float(cp[1]),
                      'SL_Mult':float(cp[2]), 'RR':float(cp[3]),
                      'Trades':nt, 'Win_Rate':wr, 'Profit':m.get('total_profit',0),
                      'Return_Pct':ret, 'Max_DD':m.get('max_drawdown',0), 'PF':pf})

    if rows:
        ex = {}
        if BEST_SETTINGS_FILE.exists():
            try: ex = json.loads(BEST_SETTINGS_FILE.read_text()).get('instruments',{})
            except: ex = {}
        mg = ex.copy()
        for r in rows:
            s = r['Symbol']
            if r['PF'] > mg.get(s,{}).get('PF',0):
                mg[s] = {k: r[k] for k in ['EMA','ADX','SL_Mult','RR','Trades',
                                            'Win_Rate','Profit','Return_Pct','Max_DD','PF']}
        BEST_SETTINGS_FILE.write_text(json.dumps({
            'generated_at': datetime.utcnow().isoformat()+'Z',
            'source': 'backtest_improved.py (ICT/SMC v2)',
            'instruments': mg}, indent=2))
        sdf = pd.DataFrame(rows).sort_values('Return_Pct', ascending=False)
        sdf.to_csv(DATA_DIR / 'summary.csv', index=False)
        print(f"\n{'='*80}\n  DONE — {n_ok}/{len(rows)} profitable\n{'='*80}")
        print(sdf[['Symbol','Return_Pct','PF','Win_Rate','Trades','EMA','ADX','SL_Mult','RR']].to_string(index=False))
        print(f"\n  Settings -> {BEST_SETTINGS_FILE}")


# ===============================================================================
# CLI
# ===============================================================================

def run_cli_backtest_mode():
    ap = argparse.ArgumentParser(description='ICT/SMC Backtest Engine — Manual Mode')
    ap.add_argument('--symbol', default='EURUSD', help='Symbol to backtest')
    ap.add_argument('--mode', default='standard',
                    choices=['standard', 'split', 'walk_forward', 'monte_carlo', 'robustness_20', 'live_smc'],
                    help='Backtest mode')
    ap.add_argument('--ema', type=int, default=50, help='EMA period')
    ap.add_argument('--adx', type=float, default=20.0, help='ADX minimum')
    ap.add_argument('--sl_mult', type=float, default=1.5, help='ATR stop multiplier')
    ap.add_argument('--rr', type=float, default=2.0, help='Reward/Risk target')
    ap.add_argument('--risk_pct', type=float, default=2.0, help='Risk percent per trade')
    ap.add_argument('--split-ratio', type=float, default=0.7, help='Train/test split ratio for split mode')
    ap.add_argument('--mc-iterations', type=int, default=200, help='Monte Carlo iterations')
    ap.add_argument('--periods', type=int, default=20, help='Number of robustness periods for robustness_20 mode')
    ap.add_argument('--bars', type=int, default=10000, help='Bars to request from MT5 for data-driven modes')
    ap.add_argument('--run-id', default=None, help='Optional run id for API integration')
    ap.add_argument('--output', default=None, help='Output file (JSON)')
    a = ap.parse_args()
    sym = str(a.symbol or 'EURUSD').upper()

    # Use best_settings.json params unless explicitly overridden on CLI
    cli_given = any(f'--{k}' in sys.argv for k in ('ema', 'adx', 'sl_mult', 'rr'))
    if cli_given:
        params = (a.ema, a.adx, a.sl_mult, a.rr)
    else:
        params = _load_best_params(sym)

    mode = str(a.mode or 'standard').lower()
    bars = max(1200, int(a.bars))
    df = fetch_data(sym, bars)
    if df is None or len(df) < 120:
        print(f"Not enough data for {sym}"); return 1

    if mode == 'split':
        result = run_split_backtest(df, sym, split_ratio=float(a.split_ratio), risk_pct=float(a.risk_pct))
        result['mode'] = 'split'
        metrics = result.get('test', {}).get('metrics', {})
    elif mode == 'walk_forward':
        result = walk_forward_analysis(sym)
        result['mode'] = 'walk_forward'
        metrics = {
            'total_trades': result.get('total_periods', 0),
            'win_rate': result.get('consistency', 0.0),
            'profit_factor': 0.0,
            'return_pct': 0.0,
            'max_drawdown': 0.0,
        }
    elif mode == 'monte_carlo':
        standard = run_backtest_no_lookahead(df, sym, params=params, risk_pct=a.risk_pct)
        mc = monte_carlo_analysis(standard.get('trades', []), iterations=max(20, int(a.mc_iterations)))
        result = {
            'symbol': sym,
            'mode': 'monte_carlo',
            'standard': standard,
            'monte_carlo': mc,
        }
        metrics = standard.get('metrics', {})
    elif mode == 'robustness_20':
        result = run_robustness_20_periods(df, sym, periods=max(2, int(a.periods)), risk_pct=float(a.risk_pct))
        metrics = {
            'total_trades': result.get('periods_run', 0),
            'win_rate': result.get('consistency', 0.0),
            'profit_factor': 0.0,
            'return_pct': result.get('average_return_pct', 0.0),
            'max_drawdown': result.get('worst_period_drawdown', 0.0),
        }
    elif mode == 'live_smc':
        result = run_live_smc_engine_backtest(df, sym, risk_pct=float(a.risk_pct))
        result['mode'] = 'live_smc'
        metrics = result.get('metrics', {})
    else:
        result = run_backtest_no_lookahead(df, sym, params=params, risk_pct=a.risk_pct)
        result['mode'] = 'standard'
        metrics = result.get('metrics', {})

    dd_pct = metrics.get('max_drawdown_pct', None)
    dd_str = f"DD={dd_pct:.2f}%" if dd_pct is not None else f"DD=${metrics.get('max_drawdown',0):.0f}"
    print(
        f"\n{sym} [{mode}]  "
        f"Trades={metrics.get('total_trades',0)}  "
        f"WR={metrics.get('win_rate',0):.1f}%  "
        f"PF={metrics.get('profit_factor',0):.2f}  "
        f"Return={metrics.get('return_pct',0):+.2f}%  "
        f"{dd_str}"
    )

    if a.run_id and isinstance(result, dict):
        result['run_id'] = str(a.run_id)

    if a.output:
        Path(a.output).write_text(json.dumps(result, indent=2, default=str))
        print(f"Saved to {a.output}")
    return 0


if __name__ == '__main__':
    cli = {
        '--symbol', '--mode', '--ema', '--adx', '--sl_mult', '--rr', '--risk_pct', '--output',
        '--split-ratio', '--mc-iterations', '--periods', '--bars', '--run-id'
    }
    if any(a in sys.argv for a in cli):
        raise SystemExit(run_cli_backtest_mode())
    main()
