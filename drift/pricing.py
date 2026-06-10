"""Pip-value helpers shared across the engine and any future module.

Extracted from ``main.py`` (cleanup #11) so that ``drift.engine`` can import
them without creating a circular dependency on the entry-point module.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def pip_multiplier(pair: str) -> float:
    """Return the multiplier to convert price distance to pips."""
    return 100.0 if "JPY" in pair.upper() else 10000.0


def pip_value(pair: str, info=None) -> float:
    """Return USD pip value per standard lot using MT5 symbol info.

    Uses ``trade_tick_value`` (USD per tick on one standard lot) and converts
    to pips: 1 pip = 10 ticks on both 5-digit (most pairs) and 3-digit (JPY)
    brokers.

    Falls back to $10 if MT5 has no info for the pair, but this is a safety
    net, not a default — every configured pair should resolve through MT5 in
    practice.

    Parameters
    ----------
    info:
        Pre-fetched ``mt5.symbol_info`` result.  When provided the function
        skips the MT5 call so callers that already hold the object avoid a
        second IPC round-trip.
    """
    if info is None:
        import MetaTrader5 as mt5

        info = mt5.symbol_info(pair)
    if info is None or info.trade_tick_value <= 0:
        logger.warning("No tick_value for %s — falling back to $10/pip/lot", pair)
        return 10.0
    return info.trade_tick_value * 10.0
