"""Drift strategy framework package.

Re-exports the public contract from ``base`` and is the place where concrete
strategy modules get imported so they register themselves in
``STRATEGY_REGISTRY``.
"""

from __future__ import annotations

from drift.strategies.base import (
    STRATEGY_REGISTRY,
    Decision,
    MarketData,
    Signal,
    Strategy,
    StrategyContext,
    get_strategy,
    register,
)

# Import concrete strategy modules so they register themselves in
# STRATEGY_REGISTRY at package import time (D054).
from drift.strategies.daily_lull import DailyLullStrategy  # noqa: E402
from drift.strategies.london_orb import LondonOrbStrategy  # noqa: E402

__all__ = [
    "DailyLullStrategy",
    "LondonOrbStrategy",
    "STRATEGY_REGISTRY",
    "Decision",
    "MarketData",
    "Signal",
    "Strategy",
    "StrategyContext",
    "get_strategy",
    "register",
]
