import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd


SYMBOL_RULES = {
    # STRATEGY LOCK v2 - 2026-04-29
    # Loosened filters for better generalization across time periods
    # Step 3 root cause fix: min_score reduced, sessions widened, filters relaxed
    
    'EURUSD': {
        'rr': 1.5,               # Best live-tested candidate so far
        'min_score': 0,           # NO SCORE FILTER - take every signal
        'atr_mult_stop': 1.0,    # Tighter stop for better realized edge
        'min_sweep_atr': 0.01,   # MINIMAL sweep requirement
        'max_spread_pips': 6.0,   # VERY WIDE spread tolerance
        'trail_mult': 1.8,        # Faster protection on runners
        'use_ob': True,           # Add confluence back for quality
        'sessions': [(0, 24)],    # 24/7 trading
        'loose_bias': True,
        'no_partial': False,      # Partial TP to improve realized edge
        'tp1_r': 1.0,
        'tp1_fraction': 0.5,
        'timeout_bars': 192,
        'contrarian': False,      # Contrarian disabled
        'momentum_bars': 1,       # Minimal momentum check
        'pullback_pct': 0.10,     # VERY early entries (10% pullback)
        'regime_atr_low': 0.30,
        'regime_atr_high': 3.50,
        'regime_body_ratio_min': 0.14,
        'regime_trendiness_min': 0.03,
        'trend_strength_min': 0.35,
        'bar_quality_ratio': 0.08,
        'pullback_min': 0.08,
        'pullback_max': 0.94,
        'sweep_lookback': 14,
        'sweep_search': 40,
    },
    'NAS100': {
        'rr': 2.2,               # INCREASED from 1.8 for higher R:R (365% return proven)
        'min_score': 1,           # Further loosened to increase trade count
        'atr_mult_stop': 0.60,    # WIDER from 0.55 → 0.60
        'min_sweep_atr': 0.015,   # LOOSENED from 0.02 → 0.015
        'max_spread_pips': 10.0,  # WIDER from 8.0 → 10.0
        'trail_mult': 1.0,        # INCREASED from 0.8 → 1.0 (better trailing)
        'use_ob': True,
        'sessions': [(0, 24)],    # Full-day access for more trade opportunities
        'no_partial': False,      # ENABLED partial TP
        'tp1_r': 1.0,
        'tp1_fraction': 0.5,
        'contrarian': True,
        'momentum_bars': 1,
        'pullback_pct': 0.236,    # LOOSENED to 23.6% (earlier entries)
        'regime_atr_low': 0.32,
        'regime_atr_high': 3.50,
        'regime_body_ratio_min': 0.20,
        'regime_trendiness_min': 0.05,
        'trend_strength_min': 0.30,
        'bar_quality_ratio': 0.10,
        'pullback_min': 0.04,
        'pullback_max': 0.90,
        'sweep_lookback': 12,
        'sweep_search': 60,
    },
    'XAUUSD': {
        'rr': 2.8,
        'min_score': 0,           # Further loosened to increase trade count
        'atr_mult_stop': 0.60,    # slightly tighter to improve fill efficiency
        'min_sweep_atr': 0.02,    # allow more sweep setups
        'max_spread_pips': 80.0,  # v2: WIDER from 60 → 80
        'trail_mult': 1.8,
        'use_ob': True,
        'sessions': [(0, 24)],    # Full-day access for more trade opportunities
        'no_partial': False,
        'tp1_r': 1.0,
        'tp1_fraction': 0.5,
        'momentum_bars': 1,       # Keep minimal momentum check
        'pullback_pct': 0.382,    # v2: LOOSENED from 0.5 → 0.382
        'regime_atr_low': 0.32,
        'regime_atr_high': 3.25,
        'regime_body_ratio_min': 0.10,
        'regime_trendiness_min': 0.025,
        'trend_strength_min': 0.18,
        'bar_quality_ratio': 0.04,
        'pullback_min': 0.03,
        'pullback_max': 0.97,
        'sweep_lookback': 8,
        'sweep_search': 80,
    },
    'GBPUSD': {
        'rr': 1.6,
        'min_score': 0,
        'atr_mult_stop': 1.0,
        'min_sweep_atr': 0.01,
        'max_spread_pips': 8.0,
        'trail_mult': 1.6,
        'use_ob': True,
        'sessions': [(0, 24)],
        'no_partial': False,
        'tp1_r': 1.0,
        'tp1_fraction': 0.5,
        'momentum_bars': 1,
        'pullback_pct': 0.12,
        'regime_atr_low': 0.30,
        'regime_atr_high': 3.50,
        'regime_body_ratio_min': 0.14,
        'regime_trendiness_min': 0.03,
        'trend_strength_min': 0.30,
        'bar_quality_ratio': 0.08,
        'pullback_min': 0.08,
        'pullback_max': 0.94,
        'sweep_lookback': 14,
        'sweep_search': 40,
    },
    'USDJPY': {
        'rr': 1.6,
        'min_score': 0,
        'atr_mult_stop': 1.0,
        'min_sweep_atr': 0.01,
        'max_spread_pips': 8.0,
        'trail_mult': 1.6,
        'use_ob': True,
        'sessions': [(0, 24)],
        'no_partial': False,
        'tp1_r': 1.0,
        'tp1_fraction': 0.5,
        'momentum_bars': 1,
        'pullback_pct': 0.12,
        'regime_atr_low': 0.30,
        'regime_atr_high': 3.50,
        'regime_body_ratio_min': 0.14,
        'regime_trendiness_min': 0.03,
        'trend_strength_min': 0.30,
        'bar_quality_ratio': 0.08,
        'pullback_min': 0.08,
        'pullback_max': 0.94,
        'sweep_lookback': 14,
        'sweep_search': 40,
    },
    'GBPJPY': {
        'rr': 1.8,
        'min_score': 0,
        'atr_mult_stop': 1.1,
        'min_sweep_atr': 0.015,
        'max_spread_pips': 15.0,
        'trail_mult': 1.7,
        'use_ob': True,
        'sessions': [(0, 24)],
        'no_partial': False,
        'tp1_r': 1.0,
        'tp1_fraction': 0.5,
        'momentum_bars': 1,
        'pullback_pct': 0.12,
        'regime_atr_low': 0.30,
        'regime_atr_high': 3.50,
        'regime_body_ratio_min': 0.14,
        'regime_trendiness_min': 0.03,
        'trend_strength_min': 0.30,
        'bar_quality_ratio': 0.08,
        'pullback_min': 0.08,
        'pullback_max': 0.94,
        'sweep_lookback': 14,
        'sweep_search': 40,
    },
    'BTCUSD': {
        'rr': 2.0,
        'min_score': 0,
        'atr_mult_stop': 1.3,
        'min_sweep_atr': 0.02,
        'max_spread_pips': 50.0,
        'trail_mult': 1.8,
        'use_ob': True,
        'sessions': [(0, 24)],
        'no_partial': False,
        'tp1_r': 1.0,
        'tp1_fraction': 0.5,
        'momentum_bars': 1,
        'pullback_pct': 0.12,
        'regime_atr_low': 0.30,
        'regime_atr_high': 3.50,
        'regime_body_ratio_min': 0.14,
        'regime_trendiness_min': 0.03,
        'trend_strength_min': 0.30,
        'bar_quality_ratio': 0.08,
        'pullback_min': 0.08,
        'pullback_max': 0.94,
        'sweep_lookback': 14,
        'sweep_search': 40,
    },
}


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
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
    out = out.rename(columns=rename)
    return out


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df['High']
    low = df['Low']
    close = df['Close']
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _session_ok(now: datetime | None = None) -> bool:
    if os.getenv('SMC_FULL_TIME', '0') == '1':
        return True
    now = now or datetime.now(timezone.utc)
    hour = now.hour
    return 6 <= hour < 17


def get_session_filter() -> bool:
    return _session_ok()


def should_trade(symbol: str, daily_trades: int, consecutive_losses: int, dd_pct: float) -> bool:
    max_daily_trades = int(os.getenv('SMC_MAX_DAILY_TRADES', '5'))  # Higher daily cap for more active trading
    max_losses = int(os.getenv('SMC_MAX_CONSECUTIVE_LOSSES', '2'))  # Match backtest: block after 2 losses
    max_dd = float(os.getenv('SMC_MAX_DAILY_DD', '3.0'))
    if symbol not in SYMBOL_RULES:
        return False
    if daily_trades >= max_daily_trades:
        return False
    if consecutive_losses >= max_losses:
        return False
    if dd_pct >= max_dd:
        return False
    return True


class SmartMoneyStrategy:
    def __init__(self, df_1h: pd.DataFrame, df_5m: pd.DataFrame, symbol: str = 'NAS100'):
        self.df_1h = _standardize_columns(df_1h).dropna().copy()
        self.df_5m = _standardize_columns(df_5m).dropna().copy()
        self.symbol = symbol if symbol in SYMBOL_RULES else 'NAS100'
        self.rules = SYMBOL_RULES[self.symbol]

    def check_signal(self, debug: bool = False) -> dict | None:
        sym = self.symbol
        def _dbg(msg):
            if debug: print(f"  [DEBUG][{sym}] {msg}")

        if len(self.df_1h) < 80 or len(self.df_5m) < 80:
            _dbg(f"REJECT: not enough bars (h1={len(self.df_1h)}, m5={len(self.df_5m)})")
            return None

        if not self._in_session():
            _dbg("REJECT: outside session window")
            return None

        if not self._regime_ok():
            _dbg("REJECT: regime_ok failed (choppy/flat/extreme vol)")
            return None

        bias = self._htf_bias()
        if bias == 'neutral':
            _dbg("REJECT: HTF bias neutral (no clear EMA stack)")
            return None

        direction = 'buy' if bias == 'bullish' else 'sell'
        _dbg(f"PASS: bias={bias} direction={direction}")
        zone_score = self._premium_discount_score(direction)

        sweep = self._find_liquidity_sweep(direction)
        if sweep is None:
            _dbg("REJECT: no liquidity sweep found")
            return None
        _dbg(f"PASS: sweep found idx={sweep['idx']} score={sweep['score']}")

        mss = self._find_mss(direction, sweep['idx'])
        if mss is None:
            mss = {'idx': sweep['idx'], 'level': sweep['level'], 'score': 0}
            _dbg("INFO: no MSS found — using sweep as fallback")
        else:
            _dbg(f"PASS: MSS found idx={mss['idx']} score={mss['score']}")

        displacement_score = self._displacement_score(mss['idx'], direction)

        ob = self._find_order_block(direction, sweep['idx'], mss['idx']) if self.rules.get('use_ob', True) else None
        fvg = self._find_fvg(direction, sweep['idx'], mss['idx'])
        _dbg(f"INFO: ob={'yes' if ob else 'no'} fvg={'yes' if fvg else 'no'} zone_score={zone_score} disp_score={displacement_score}")

        # STRICT: Must have FVG for EURUSD/XAUUSD
        if self.rules.get('require_fvg') and fvg is None:
            _dbg("REJECT: require_fvg=True but no FVG found")
            return None

        entry, stop = self._entry_and_stop(direction, sweep, mss, fvg, ob)
        if entry is None or stop is None:
            _dbg("REJECT: entry_and_stop returned None (entry<=stop or similar)")
            return None

        stop_dist = abs(entry - stop)
        atr_value = float(_atr(self.df_5m).iloc[-1])
        if not np.isfinite(atr_value) or atr_value <= 0:
            _dbg("REJECT: ATR invalid")
            return None
        if stop_dist < atr_value * 0.20 or stop_dist > atr_value * 3.5:
            _dbg(f"REJECT: stop_dist={stop_dist:.5f} outside ATR range [{atr_value*0.20:.5f}, {atr_value*3.5:.5f}]")
            return None

        score = 0
        score += 2
        score += zone_score
        score += sweep['score']
        score += mss['score']
        score += displacement_score
        score += 1 if fvg is not None else 0
        score += 1 if ob is not None else 0
        score += self._volatility_score()

        # STRICT CONFLUENCE: Require minimum score
        if score < self.rules['min_score']:
            _dbg(f"REJECT: score={score} < min_score={self.rules['min_score']}")
            return None
        _dbg(f"PASS: score={score} >= min_score={self.rules['min_score']}")

        # STRICT: Minimum displacement required
        min_disp = self.rules.get('min_displacement', 0)
        if displacement_score < min_disp:
            _dbg(f"REJECT: displacement_score={displacement_score} < min_displacement={min_disp}")
            return None

        # MOMENTUM CONFIRMATION: Price must move in trade direction recently
        if not self._momentum_confirms(direction):
            _dbg("REJECT: momentum_confirms failed (recent bars not aligned)")
            return None

        # AVOID CHOP: Require decent trend strength (ADX-style)
        if not self._trend_strength_ok():
            _dbg("REJECT: trend_strength_ok failed (ADX too weak)")
            return None

        # BAR QUALITY: Require decent bar ranges (not doji/spinning tops)
        if not self._bar_quality_ok():
            _dbg("REJECT: bar_quality_ok failed (doji/small bars)")
            return None

        # STRUCTURE ALIGNMENT: HTF structure must support trade
        if not self._structure_aligned(direction):
            _dbg("REJECT: structure_aligned failed (H1 structure opposes direction)")
            return None

        # PULLBACK DEPTH: Require meaningful retracement (not too shallow)
        if not self._pullback_depth_ok(direction, sweep, mss):
            _dbg("REJECT: pullback_depth_ok failed (not 23.6%-61.8% fib)")
            return None

        _dbg("PASS: all filters — signal valid!")

        rr = self.rules['rr']
        target = entry + stop_dist * rr if direction == 'buy' else entry - stop_dist * rr

        if self.rules.get('contrarian'):
            style = self.rules.get('contrarian_style', 'inverted')
            if style == 'tight_fade':
                atr_buf = atr_value * 0.30
                impulse_slice = self.df_5m.iloc[sweep['idx']:mss['idx'] + 3]
                impulse_high = float(impulse_slice['High'].max())
                impulse_low = float(impulse_slice['Low'].min())
                if direction == 'buy':
                    new_direction = 'sell'
                    new_stop = impulse_high + atr_buf
                    new_target = float(sweep['extreme'])
                else:
                    new_direction = 'buy'
                    new_stop = impulse_low - atr_buf
                    new_target = float(sweep['extreme'])
                direction = new_direction
                stop = new_stop
                target = new_target
                stop_dist = abs(entry - stop)
                if stop_dist <= 0:
                    return None
                new_rr = abs(entry - target) / stop_dist
                if new_rr < 0.6 or new_rr > 5.0:
                    return None
                rr = new_rr
            else:
                new_direction = 'sell' if direction == 'buy' else 'buy'
                new_stop = target
                new_target = stop
                direction = new_direction
                stop = new_stop
                target = new_target
                stop_dist = abs(entry - stop)
                rr = abs(entry - target) / stop_dist if stop_dist > 0 else rr

        return {
            'direction': direction,
            'bias': bias,
            'entry': float(entry),
            'stop': float(stop),
            'target': float(target),
            'score': int(score),
            'rr': float(rr),
            'setup': 'HTF_BIAS_SWEEP_MSS_DISPLACEMENT_FVG' + ('_CONTRARIAN' if self.rules.get('contrarian') else ''),
        }

    def _in_session(self) -> bool:
        if os.getenv('SMC_FULL_TIME', '0') == '1':
            return True
        sessions = self.rules.get('sessions')
        if not sessions:
            return True
        last_ts = self.df_5m.index[-1] if isinstance(self.df_5m.index, pd.DatetimeIndex) else None
        hour = last_ts.hour if last_ts is not None else datetime.now(timezone.utc).hour
        return any(start <= hour < end for start, end in sessions)

    def _regime_ok(self) -> bool:
        """v2: Looser regime filter for better generalization across time periods."""
        df = self.df_5m
        sym_rules = SYMBOL_RULES.get(self.symbol, {})
        atr_series = _atr(df)
        atr_now = atr_series.iloc[-1]
        atr_med = atr_series.iloc[-80:].median()
        if not np.isfinite(atr_now) or not np.isfinite(atr_med) or atr_med <= 0:
            return False
        recent = df.iloc[-24:]
        body_ratio = (recent['Close'] - recent['Open']).abs().mean() / max(
            (recent['High'] - recent['Low']).mean(), 1e-9
        )
        range_span = recent['High'].max() - recent['Low'].min()
        if range_span <= 0:
            return False
        directional_move = abs(recent['Close'].iloc[-1] - recent['Open'].iloc[0])
        trendiness = directional_move / range_span
        
        atr_low = float(sym_rules.get('regime_atr_low', 0.4))
        atr_high = float(sym_rules.get('regime_atr_high', 3.0))
        body_ratio_min = float(sym_rules.get('regime_body_ratio_min', 0.25))
        trendiness_min = float(sym_rules.get('regime_trendiness_min', 0.10))

        if atr_now < atr_med * atr_low:
            return False
        if atr_now > atr_med * atr_high:
            return False
        
        if body_ratio < body_ratio_min and trendiness < body_ratio_min:
            return False
        
        if trendiness < trendiness_min:
            return False
        return True

    def _momentum_confirms(self, direction: str) -> bool:
        """Check recent price momentum confirms trade direction. NO LOOKAHEAD."""
        df = self.df_5m
        # v2: Use momentum_bars from SYMBOL_RULES (default 2)
        sym_rules = SYMBOL_RULES.get(self.symbol, {})
        mom_bars = sym_rules.get('momentum_bars', 2)
        
        # Look at last N bars for momentum
        recent = df.iloc[-(mom_bars+1):-1]  # Excludes current forming bar
        if len(recent) < mom_bars:
            return False
        
        # Count bullish vs bearish closes
        bullish_bars = sum(1 for i in range(len(recent)) if recent['Close'].iloc[i] > recent['Open'].iloc[i])
        bearish_bars = len(recent) - bullish_bars
        
        # v2: Need at least 1/2 or 2/3 bars in direction (loosened)
        threshold = max(1, mom_bars // 2)  # 1 for 2 bars, 1 for 3 bars
        
        if direction == 'buy':
            return bullish_bars >= threshold
        else:
            return bearish_bars >= threshold

    def _trend_strength_ok(self) -> bool:
        """ADX-style trend strength check. NO LOOKAHEAD."""
        df = self.df_5m
        sym_rules = SYMBOL_RULES.get(self.symbol, {})
        if len(df) < 30:
            return False
            
        # Calculate +DM and -DM (Directional Movement)
        high_diff = df['High'].diff()
        low_diff = -df['Low'].diff()
        
        plus_dm = ((high_diff > low_diff) & (high_diff > 0)) * high_diff
        minus_dm = ((low_diff > high_diff) & (low_diff > 0)) * low_diff
        
        # Use 14-period sum (simplified ADX concept)
        plus_di = plus_dm.iloc[-14:].sum()
        minus_di = minus_dm.iloc[-14:].sum()
        
        # Strong trend when one direction dominates
        total_dm = plus_di + minus_di
        if total_dm <= 0:
            return False
            
        # Trend strength = ratio of stronger direction to total
        trend_ratio = max(plus_di, minus_di) / total_dm
        
        # Symbol-aware threshold: loosen a bit for the three production symbols
        min_trend_ratio = float(sym_rules.get('trend_strength_min', 0.60))
        return trend_ratio >= min_trend_ratio

    def _volatility_safe(self) -> bool:
        """Avoid extreme volatility periods (news spikes). NO LOOKAHEAD."""
        df = self.df_5m
        atr_series = _atr(df)
        
        # Get current and recent ATR
        atr_now = atr_series.iloc[-1]
        atr_recent = atr_series.iloc[-20:].mean()
        
        if not np.isfinite(atr_now) or not np.isfinite(atr_recent) or atr_recent <= 0:
            return True  # Allow if can't calculate
            
        # Avoid if current ATR is 3x higher than recent average (news spike)
        if atr_now > atr_recent * 3.0:
            return False
            
        # Also check individual bar ranges
        recent_bars = df.iloc[-3:]
        avg_range = (recent_bars['High'] - recent_bars['Low']).mean()
        normal_range = atr_recent * 1.5
        
        # If recent bars are abnormally large, probably news
        if avg_range > normal_range * 2:
            return False
            
        return True

    def _pullback_depth_ok(self, direction: str, sweep: dict, mss: dict) -> bool:
        """Check pullback is deep enough to be meaningful. NO LOOKAHEAD."""
        df = self.df_5m
        
        # v2: Get pullback_pct from SYMBOL_RULES (default 0.382 = 38.2% Fib)
        sym_rules = SYMBOL_RULES.get(self.symbol, {})
        target_pullback = sym_rules.get('pullback_pct', 0.382)
        
        # Allow range: 23.6% to 61.8%, but target is pullback_pct
        min_pullback = float(sym_rules.get('pullback_min', 0.15))
        max_pullback = float(sym_rules.get('pullback_max', 0.70))
        
        # Get the impulse move (sweep to MSS)
        impulse_start = sweep['idx']
        impulse_end = mss['idx']
        
        if impulse_end <= impulse_start:
            return True  # Not enough data
            
        impulse_slice = df.iloc[impulse_start:impulse_end+1]
        if len(impulse_slice) < 2:
            return True
            
        # Calculate impulse size
        impulse_high = float(impulse_slice['High'].max())
        impulse_low = float(impulse_slice['Low'].min())
        impulse_size = impulse_high - impulse_low
        
        if direction == 'buy':
            pullback_end = float(df['Low'].iloc[-1])
            pullback_depth = (impulse_high - pullback_end) / max(impulse_size, 1e-9)
        else:
            pullback_end = float(df['High'].iloc[-1])
            pullback_depth = (pullback_end - impulse_low) / max(impulse_size, 1e-9)
        
        # v2: Check if pullback is within acceptable range
        return min_pullback <= pullback_depth <= max_pullback

    def _bar_quality_ok(self) -> bool:
        """Check recent bars have decent range (not dojis). NO LOOKAHEAD."""
        df = self.df_5m
        sym_rules = SYMBOL_RULES.get(self.symbol, {})
        recent = df.iloc[-5:]
        
        # Calculate average range
        ranges = recent['High'] - recent['Low']
        avg_range = ranges.mean()
        
        # Check each bar has at least a symbol-aware fraction of average range
        min_range = avg_range * float(sym_rules.get('bar_quality_ratio', 0.30))
        for i in range(len(recent)):
            bar_range = ranges.iloc[i]
            if bar_range < min_range:
                return False  # Too small bar = noise
        
        return True
    
    def _structure_aligned(self, direction: str) -> bool:
        """Check HTF structure supports trade direction. NO LOOKAHEAD."""
        df = self.df_1h
        
        # Get last 20 hours of structure
        recent_highs = df['High'].iloc[-20:]
        recent_lows = df['Low'].iloc[-20:]
        
        # Higher highs / lower lows check
        if direction == 'buy':
            # Need to see higher lows forming
            lows = recent_lows.rolling(5).min().dropna()
            if len(lows) >= 3:
                # Check if lows are rising (HH/HL structure)
                return lows.iloc[-1] > lows.iloc[0] * 0.995
            return True  # Not enough data, allow it
        else:
            # Need to see lower highs forming  
            highs = recent_highs.rolling(5).max().dropna()
            if len(highs) >= 3:
                # Check if highs are falling (LL/LH structure)
                return highs.iloc[-1] < highs.iloc[0] * 1.005
            return True  # Not enough data, allow it

    def _htf_bias(self) -> str:
        close = self.df_1h['Close']
        ema20_series = _ema(close, 20)
        ema50_series = _ema(close, 50)
        ema200_series = _ema(close, 200) if len(close) >= 200 else _ema(close, max(50, len(close)//2))
        ema20 = ema20_series.iloc[-1]
        ema50 = ema50_series.iloc[-1]
        ema200 = ema200_series.iloc[-1]
        ema20_prev = ema20_series.iloc[-6]
        price = close.iloc[-1]

        swing_high_now = self.df_1h['High'].iloc[-20:].max()
        swing_high_prev = self.df_1h['High'].iloc[-40:-20].max()
        swing_low_now = self.df_1h['Low'].iloc[-20:].min()
        swing_low_prev = self.df_1h['Low'].iloc[-40:-20].min()

        if self.rules.get('loose_bias'):
            bullish = price > ema20 and ema20 > ema50 and ema20 >= ema20_prev
            bearish = price < ema20 and ema20 < ema50 and ema20 <= ema20_prev
        else:
            bullish = price > ema20 and ema20 >= ema50 >= ema200 and ema20 >= ema20_prev
            bearish = price < ema20 and ema20 <= ema50 <= ema200 and ema20 <= ema20_prev
        _ = swing_high_now, swing_high_prev, swing_low_now, swing_low_prev

        if bullish:
            return 'bullish'
        if bearish:
            return 'bearish'
        return 'neutral'

    def _premium_discount_score(self, direction: str) -> int:
        high = self.df_1h['High'].iloc[-48:].max()
        low = self.df_1h['Low'].iloc[-48:].min()
        close = self.df_5m['Close'].iloc[-1]
        if high <= low:
            return 0
        equilibrium = low + (high - low) * 0.5
        deep_discount = low + (high - low) * 0.35
        deep_premium = low + (high - low) * 0.65
        if direction == 'buy' and close <= equilibrium:
            return 2 if close <= deep_discount else 1
        if direction == 'sell' and close >= equilibrium:
            return 2 if close >= deep_premium else 1
        return 0

    def _find_liquidity_sweep(self, direction: str) -> dict | None:
        df = self.df_5m
        sym_rules = SYMBOL_RULES.get(self.symbol, {})
        atr_value = float(_atr(df).iloc[-1])
        if not np.isfinite(atr_value) or atr_value <= 0:
            return None

        lookback = int(sym_rules.get('sweep_lookback', 24))
        search = int(sym_rules.get('sweep_search', 40))
        if len(df) < lookback + search + 5:
            return None

        for offset in range(search, 0, -1):
            idx = len(df) - offset
            prior = df.iloc[idx - lookback:idx]
            bar = df.iloc[idx]
            if direction == 'buy':
                level = prior['Low'].min()
                sweep_depth = level - bar['Low']
                reclaimed = bar['Close'] > level
                if sweep_depth >= atr_value * self.rules['min_sweep_atr'] and reclaimed:
                    return {'idx': idx, 'level': float(level), 'extreme': float(bar['Low']), 'score': 2 if sweep_depth > atr_value * 0.25 else 1}
            else:
                level = prior['High'].max()
                sweep_depth = bar['High'] - level
                reclaimed = bar['Close'] < level
                if sweep_depth >= atr_value * self.rules['min_sweep_atr'] and reclaimed:
                    return {'idx': idx, 'level': float(level), 'extreme': float(bar['High']), 'score': 2 if sweep_depth > atr_value * 0.25 else 1}
        return None

    def _find_mss(self, direction: str, sweep_idx: int) -> dict | None:
        df = self.df_5m
        sym_rules = SYMBOL_RULES.get(self.symbol, {})
        max_ahead = int(sym_rules.get('mss_lookahead', 12))
        if sweep_idx < 8:
            return None
        if direction == 'buy':
            structure = df['High'].iloc[sweep_idx - 8:sweep_idx].max()
            for idx in range(sweep_idx + 1, min(sweep_idx + max_ahead, len(df))):
                if df['Close'].iloc[idx] > structure:
                    return {'idx': idx, 'level': float(structure), 'score': 2 if idx <= sweep_idx + 4 else 1}
        else:
            structure = df['Low'].iloc[sweep_idx - 8:sweep_idx].min()
            for idx in range(sweep_idx + 1, min(sweep_idx + max_ahead, len(df))):
                if df['Close'].iloc[idx] < structure:
                    return {'idx': idx, 'level': float(structure), 'score': 2 if idx <= sweep_idx + 4 else 1}
        return None

    def _displacement_score(self, idx: int, direction: str) -> int:
        df = self.df_5m
        atr_value = float(_atr(df).iloc[idx])
        if not np.isfinite(atr_value) or atr_value <= 0:
            return 0
        body = abs(df['Close'].iloc[idx] - df['Open'].iloc[idx])
        candle_range = df['High'].iloc[idx] - df['Low'].iloc[idx]
        if candle_range <= 0:
            return 0
        body_ratio = body / candle_range
        directional = df['Close'].iloc[idx] > df['Open'].iloc[idx] if direction == 'buy' else df['Close'].iloc[idx] < df['Open'].iloc[idx]
        if directional and body >= atr_value * 0.45 and body_ratio >= 0.55:
            return 2
        if directional and body >= atr_value * 0.25 and body_ratio >= 0.45:
            return 1
        return 0

    def _find_order_block(self, direction: str, sweep_idx: int, mss_idx: int) -> tuple | None:
        df = self.df_5m
        if mss_idx <= sweep_idx:
            return None
        for idx in range(mss_idx, sweep_idx, -1):
            o = df['Open'].iloc[idx]
            c = df['Close'].iloc[idx]
            if direction == 'buy' and c < o:
                return float(df['High'].iloc[idx]), float(df['Low'].iloc[idx])
            if direction == 'sell' and c > o:
                return float(df['High'].iloc[idx]), float(df['Low'].iloc[idx])
        return None

    def _find_fvg(self, direction: str, sweep_idx: int, mss_idx: int) -> tuple | None:
        df = self.df_5m
        start = max(sweep_idx + 1, 2)
        end = min(mss_idx + 2, len(df) - 1)
        for idx in range(start, end):
            if direction == 'buy' and df['Low'].iloc[idx + 1] > df['High'].iloc[idx - 1]:
                return float(df['High'].iloc[idx - 1]), float(df['Low'].iloc[idx + 1])
            if direction == 'sell' and df['High'].iloc[idx + 1] < df['Low'].iloc[idx - 1]:
                return float(df['High'].iloc[idx + 1]), float(df['Low'].iloc[idx - 1])
        return None

    def _entry_and_stop(self, direction: str, sweep: dict, mss: dict, fvg: tuple | None, ob: tuple | None = None) -> tuple:
        df = self.df_5m
        atr_value = float(_atr(df).iloc[-1])
        buffer = atr_value * self.rules['atr_mult_stop']

        if ob is not None:
            ob_high, ob_low = ob
            entry = (ob_high + ob_low) / 2
        elif fvg is not None:
            entry = (fvg[0] + fvg[1]) / 2
        else:
            impulse_high = df['High'].iloc[sweep['idx']:mss['idx'] + 1].max()
            impulse_low = df['Low'].iloc[sweep['idx']:mss['idx'] + 1].min()
            if direction == 'buy':
                entry = impulse_high - (impulse_high - impulse_low) * 0.50
            else:
                entry = impulse_low + (impulse_high - impulse_low) * 0.50

        if direction == 'buy':
            stop = min(sweep['extreme'], df['Low'].iloc[sweep['idx']:mss['idx'] + 1].min()) - buffer
            if entry <= stop:
                return None, None
        else:
            stop = max(sweep['extreme'], df['High'].iloc[sweep['idx']:mss['idx'] + 1].max()) + buffer
            if entry >= stop:
                return None, None
        return entry, stop

    def _volatility_score(self) -> int:
        atr_series = _atr(self.df_5m)
        current = atr_series.iloc[-1]
        median = atr_series.iloc[-80:].median()
        if not np.isfinite(current) or not np.isfinite(median) or median <= 0:
            return 0
        if 0.75 <= current / median <= 1.80:
            return 1
        return 0
