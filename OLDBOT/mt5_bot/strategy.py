"""Compatibility settings adapter for the live bot.

`main.py` expects `get_instrument_settings()` from a module named `strategy`.
The actual strategy implementation lives in `smart_money_strategy.py`, so this
module bridges the old import path to the current configuration source.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from OLDBOT.mt5_bot.smart_money_strategy import SYMBOL_RULES


_BASE_DIR = Path(__file__).resolve().parent
_BEST_SETTINGS_PATH = _BASE_DIR / "best_settings.json"


def _normalize_settings(symbol: str, settings: dict | None) -> dict:
    rule = SYMBOL_RULES.get(symbol, {})
    settings = dict(settings or {})

    normalized = {
        "EMA_Fast": int(settings.get("EMA_Fast", 15)),
        "EMA_Slow": int(settings.get("EMA_Slow", 50)),
        "ADX": float(settings.get("ADX", 25.0)),
        "ATR_Mult": float(settings.get("ATR_Mult", rule.get("atr_mult_stop", 1.5))),
        "RR": float(settings.get("RR", rule.get("rr", 2.0))),
        "Risk_Pct": float(settings.get("Risk_Pct", 1.0)),
        "Pullback_Pct": float(settings.get("Pullback_Pct", 0.3)),
    }

    # Preserve any extra values in the JSON so the rest of the bot can inspect
    # them without losing information.
    normalized.update(settings)
    return normalized


@lru_cache(maxsize=1)
def _load_best_settings() -> dict:
    if not _BEST_SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(_BEST_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    instruments = data.get("instruments", {})
    return instruments if isinstance(instruments, dict) else {}


def get_instrument_settings(symbol: str) -> dict:
    """Return per-symbol live settings expected by main.py."""
    symbol = symbol.upper()
    best_settings = _load_best_settings()
    if symbol in best_settings:
        return _normalize_settings(symbol, best_settings[symbol])

    if symbol in SYMBOL_RULES:
        rule = SYMBOL_RULES[symbol]
        return _normalize_settings(symbol, {
            "EMA_Fast": 15,
            "EMA_Slow": 50,
            "ADX": 25.0,
            "ATR_Mult": rule.get("atr_mult_stop", 1.5),
            "RR": rule.get("rr", 2.0),
            "Risk_Pct": 1.0,
            "Pullback_Pct": 0.3,
        })

    return {}
