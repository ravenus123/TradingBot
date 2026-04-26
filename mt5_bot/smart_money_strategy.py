import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd


SYMBOL_RULES = {
    'EURUSD': {
        'rr': 1.5,
        'min_score': 4,
        'atr_mult_stop': 0.70,
        'min_sweep_atr': 0.04,
        'max_spread_pips': 2.5,
        'trail_mult': None,
        'use_ob': False,
        'sessions': [(7, 11), (13, 16)],
        'loose_bias': True,
        'no_partial': True,
        'timeout_bars': 192,
        'contrarian': True,
        'contrarian_style': 'tight_fade',
    },
    'NAS100': {
        'rr': 1.5,
        'min_score': 4,
        'atr_mult_stop': 0.50,
        'min_sweep_atr': 0.03,
        'max_spread_pips': 8.0,
        'trail_mult': None,
        'use_ob': True,
        'sessions': [(13, 20)],
        'no_partial': True,
        'contrarian': True,
    },
    'XAUUSD': {
        'rr': 2.0,
        'min_score': 4,
        'atr_mult_stop': 0.55,
        'min_sweep_atr': 0.04,
        'max_spread_pips': 60.0,
        'trail_mult': None,
        'use_ob': True,
        'sessions': [(12, 17)],
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
    max_daily_trades = int(os.getenv('SMC_MAX_DAILY_TRADES', '3'))  # Match backtest: MAX_DAILY_TRADES = 3
    max_losses = int(os.getenv('SMC_MAX_CONSECUTIVE_LOSSES', '3'))
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

    def check_signal(self) -> dict | None:
        if len(self.df_1h) < 80 or len(self.df_5m) < 80:
            return None

        if not self._in_session():
            return None

        if not self._regime_ok():
            return None

        bias = self._htf_bias()
        if bias == 'neutral':
            return None

        direction = 'buy' if bias == 'bullish' else 'sell'
        zone_score = self._premium_discount_score(direction)

        sweep = self._find_liquidity_sweep(direction)
        if sweep is None:
            return None

        mss = self._find_mss(direction, sweep['idx'])
        if mss is None:
            mss = {'idx': sweep['idx'], 'level': sweep['level'], 'score': 0}

        displacement_score = self._displacement_score(mss['idx'], direction)

        ob = self._find_order_block(direction, sweep['idx'], mss['idx']) if self.rules.get('use_ob', True) else None
        fvg = self._find_fvg(direction, sweep['idx'], mss['idx'])
        entry, stop = self._entry_and_stop(direction, sweep, mss, fvg, ob)
        if entry is None or stop is None:
            return None

        stop_dist = abs(entry - stop)
        atr_value = float(_atr(self.df_5m).iloc[-1])
        if not np.isfinite(atr_value) or atr_value <= 0:
            return None
        if stop_dist < atr_value * 0.20 or stop_dist > atr_value * 3.5:
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

        # STRICT CONFLUENCE: Require minimum 3 score points beyond base
        if score < self.rules['min_score'] + 1:
            return None
            
        # MOMENTUM CONFIRMATION: Price must move in trade direction recently
        if not self._momentum_confirms(direction):
            return None
            
        # AVOID CHOP: Require decent trend strength (ADX-style)
        if not self._trend_strength_ok():
            return None

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
        df = self.df_5m
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
        if atr_now < atr_med * 0.6:
            return False
        # STRICter: Avoid extreme volatility expansion (often news/chop)
        if atr_now > atr_med * 2.5:
            return False
        if body_ratio < 0.35 and trendiness < 0.25:
            return False
        # NEW: Require minimum directional movement
        if trendiness < 0.15:
            return False
        return True

    def _momentum_confirms(self, direction: str) -> bool:
        """Check recent price momentum confirms trade direction. NO LOOKAHEAD."""
        df = self.df_5m
        # Look at last 3 bars for momentum
        recent = df.iloc[-4:-1]  # Excludes current forming bar
        if len(recent) < 3:
            return False
        
        # Count bullish vs bearish closes
        bullish_bars = sum(1 for i in range(len(recent)) if recent['Close'].iloc[i] > recent['Open'].iloc[i])
        bearish_bars = 3 - bullish_bars
        
        if direction == 'buy':
            # Need at least 2 of 3 bars bullish
            return bullish_bars >= 2
        else:
            # Need at least 2 of 3 bars bearish
            return bearish_bars >= 2

    def _trend_strength_ok(self) -> bool:
        """ADX-style trend strength check. NO LOOKAHEAD."""
        df = self.df_5m
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
        
        # Require 60% trend dominance
        return trend_ratio >= 0.60

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
        atr_value = float(_atr(df).iloc[-1])
        if not np.isfinite(atr_value) or atr_value <= 0:
            return None

        lookback = 24
        search = 40
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
        if sweep_idx < 8:
            return None
        if direction == 'buy':
            structure = df['High'].iloc[sweep_idx - 8:sweep_idx].max()
            for idx in range(sweep_idx + 1, min(sweep_idx + 12, len(df))):
                if df['Close'].iloc[idx] > structure:
                    return {'idx': idx, 'level': float(structure), 'score': 2 if idx <= sweep_idx + 4 else 1}
        else:
            structure = df['Low'].iloc[sweep_idx - 8:sweep_idx].min()
            for idx in range(sweep_idx + 1, min(sweep_idx + 12, len(df))):
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
