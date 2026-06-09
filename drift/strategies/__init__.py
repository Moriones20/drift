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

# las estrategias concretas se importan aquí para registrarse
# (p.ej. from drift.strategies import daily_lull) — se añade en el chunk del port

__all__ = [
    "STRATEGY_REGISTRY",
    "Decision",
    "MarketData",
    "Signal",
    "Strategy",
    "StrategyContext",
    "get_strategy",
    "register",
]
