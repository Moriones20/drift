"""Strategy framework contract — the types every Drift strategy implements.

This module defines the contract between the engine ("dumb engine, smart
strategy", D051) and the strategies it hosts:

- ``Signal``      — the shared signal record used for transparent logging of
                    every evaluation, accepted or rejected (mirrors the
                    ``signals`` table, D056).
- ``Decision``    — what ``on_bar`` returns to the engine: open, close,
                    close-all, or noop.
- ``MarketData``  — the data accessor the engine implements and passes to the
                    strategy.  A strategy NEVER touches MT5 directly (D055).
- ``StrategyContext`` — per-tick account/attribution context, pre-filtered for
                    this strategy (D052, D053).
- ``Strategy``    — the protocol every strategy class satisfies (D051).
- ``STRATEGY_REGISTRY`` / ``register`` / ``get_strategy`` — name to class
                    lookup (D054).

The session-window logic, per-pair state, parameter parsing and special rules
all live inside the strategy; the engine only knows when a candle closes.  See
``docs/knowledge/strategy-framework.md`` and decisions D050-D057.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

import pandas as pd

# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    """Shared signal record for logging and execution.

    Copied verbatim from ``drift/strategy.py`` so it can be the cross-strategy
    record reflected by the ``signals`` table (D056).  Every evaluation, whether
    it produces a trade or a rejection, is represented as a ``Signal`` for the
    full-transparency logging principle.

    The Daily-Lull-specific fields (``range_high``/``range_low``/
    ``range_atr_ratio``, ``rsi``, ``atr_value``, ``h4_adx``) default to NaN so
    strategies that do not use them can leave them unset.
    """

    action: Literal["buy", "sell", "none"]
    pair: str
    timestamp: datetime  # current bar close, server time labeled tz-aware
    m15_candle_time: datetime  # = timestamp (alias for db logging)
    h4_candle_time: datetime  # H4 bar used for ADX filter (NaN-equivalent ok)
    entry_price: float
    sl: float = float("nan")
    tp: float = float("nan")
    range_high: float = float("nan")
    range_low: float = float("nan")
    range_atr_ratio: float = float("nan")  # (range_high - range_low) / atr
    rsi: float = float("nan")  # M15
    atr_value: float = float("nan")  # M15
    h4_adx: float = float("nan")
    reason: str = ""  # human-readable rejection/acceptance reason
    rejection_reason: str | None = None  # set when a strategy check rejects the signal


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    """The intent a strategy returns to the engine from ``on_bar``.

    A single dataclass with a discriminating ``kind`` (instead of a subclass
    hierarchy) so the engine can switch on one field.  The strategy only
    expresses intention; the engine decides whether it can execute it (risk
    gating, limits) and how (sizing, magic).

    Build instances through the convenience constructors rather than the raw
    initializer:

    - :meth:`Decision.open` — open a position from ``signal``.
    - :meth:`Decision.close` — close one position by ``ticket``.
    - :meth:`Decision.close_all` — close every position of this strategy.
    - :meth:`Decision.noop` — do nothing this tick (optionally carrying a
      ``signal`` to log a rejection).
    """

    kind: Literal["open", "close", "close_all", "noop"]
    signal: Signal | None = None  # whenever there is something to log (incl. rejections)
    ticket: int | None = None  # target position for kind="close"
    reason: str = ""  # reason for close / close_all

    @classmethod
    def open(cls, signal: Signal) -> Decision:
        """Open a position from *signal* (the engine sizes and attributes it)."""
        return cls(kind="open", signal=signal)

    @classmethod
    def close(cls, ticket: int, reason: str = "") -> Decision:
        """Close a single position identified by its MT5 *ticket*."""
        return cls(kind="close", ticket=ticket, reason=reason)

    @classmethod
    def close_all(cls, reason: str = "") -> Decision:
        """Close all positions of this strategy (e.g. the Lull 02:00 time stop)."""
        return cls(kind="close_all", reason=reason)

    @classmethod
    def noop(cls, signal: Signal | None = None) -> Decision:
        """Do nothing this tick.

        Pass *signal* to preserve transparency: the engine logs every signal,
        including rejections that resolve to no action.
        """
        return cls(kind="noop", signal=signal)


# ---------------------------------------------------------------------------
# MarketData
# ---------------------------------------------------------------------------


@runtime_checkable
class MarketData(Protocol):
    """Data accessor served by the engine; the only way a strategy reads candles.

    The engine implements this and passes it into ``on_bar``.  The same protocol
    is satisfied by a fake implementation in the backtest adapter so the very
    same ``on_bar`` runs live and in backtest (D055).  A strategy NEVER calls
    MT5 (or ``mt5_client``, the DB, or the system clock) directly — that hard
    rule is what keeps ``on_bar`` testable and backtestable.
    """

    def candles(self, pair: str, timeframe: str, count: int) -> pd.DataFrame:
        """Return the last *count* CLOSED candles for ``(pair, timeframe)``.

        Contract:

        - Only candles already CLOSED relative to the current tick are
          returned; the still-forming candle is never served (the engine
          applies the closed-bar filter, D045).
        - The index is a tz-aware ``DatetimeIndex`` in MT5 server time (labeled
          tz-aware but reasoned over as the server clock, not real UTC).
        - Columns are lowercase ``open``, ``high``, ``low``, ``close``,
          ``volume``.
        - Fetches are lazy and deduplicated per ``(pair, timeframe)`` per tick
          by the engine, so repeated calls within a tick are cheap.

        A strategy may read a timeframe it does not tick on (e.g. the Lull reads
        H4 for its ADX filter); ``timeframes`` controls when ``on_bar`` is
        called, ``candles`` controls what data is read.
        """
        ...


# ---------------------------------------------------------------------------
# StrategyContext
# ---------------------------------------------------------------------------


@dataclass
class StrategyContext:
    """Per-tick account context the engine passes to a strategy.

    Pre-filtered for THIS strategy: ``open_positions`` already excludes other
    strategies' positions (filtered by this strategy's effective magic, D052).
    The strategy reasons as if it were the only one on the account, over its
    notional ``allocated_capital``.

    The strategy does NOT size positions or check drawdown — that is the engine's
    job (D053).  This context is informational only.
    """

    account_balance: float  # total account balance
    account_equity: float  # total account equity (balance + floating P&L)
    allocated_capital: float  # notional for this strategy = balance * allocation_pct/100 (D053)
    open_positions: list[dict] = field(default_factory=list)  # this strategy's positions
    paused: bool = False  # True if this strategy is paused (own brake or /pause <name>)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


@runtime_checkable
class Strategy(Protocol):
    """The contract every Drift strategy class satisfies (D051).

    Attributes
    ----------
    name:
        Config key, value of the DB ``strategy`` column, and registry key.  All
        three MUST match.
    pairs:
        The pairs this instance trades (from its own config).
    timeframes:
        The timeframes this strategy needs to be woken on, e.g.
        ``frozenset({"M15"})``.  The engine ticks on the UNION of the
        timeframes of all active strategies and calls ``on_bar`` at each
        relevant candle close.  Reading additional timeframes as context (via
        ``market.candles``) does not require declaring them here.
    """

    name: str
    pairs: list[str]
    timeframes: frozenset[str]

    def on_bar(
        self,
        pair: str,
        timeframe: str,
        bar_close_time: datetime,
        market: MarketData,
        ctx: StrategyContext,
    ) -> Decision:
        """Evaluate one closed-candle wake and return a :class:`Decision`.

        The engine only wakes the strategy at candle closes of its declared
        ``timeframes`` (D051); it knows nothing about session windows.  The
        strategy decides internally, based on its own window/state logic and the
        server-time ``bar_close_time``, what to do this tick.

        Parameters
        ----------
        pair:
            The pair whose candle just closed.
        timeframe:
            Which of this strategy's timeframes just closed.
        bar_close_time:
            The candle close instant, tz-aware in MT5 server time (the strategy
            reasons over this server clock and never re-localizes it).
        market:
            Data accessor; the only way to read candles (D055).
        ctx:
            Account context pre-filtered for this strategy (D052, D053).

        Returns
        -------
        Decision
            ``Decision.open(signal)`` to request a trade, ``Decision.close`` /
            ``Decision.close_all`` to exit, or ``Decision.noop`` (optionally
            carrying a rejected signal for logging).
        """
        ...

    def on_fill(self, pair: str, signal: Signal, ticket: int) -> None:
        """Notify the strategy that a requested open was actually filled.

        The engine calls this AFTER it successfully opens a position from the
        ``Decision.open(signal)`` returned by :meth:`on_bar` (i.e. risk gating
        passed, sizing succeeded and the broker accepted the order), passing the
        MT5 ``ticket`` of the new position.

        This is the hook a strategy uses to commit any state that must only flip
        once a trade truly exists — for the Daily Lull, marking the per-pair
        session as ``traded`` so it does not enter twice.  Doing it here (rather
        than inside :meth:`on_bar`) keeps that state correct even though the
        engine, not the strategy, decides whether the open goes through.

        Stateless strategies may leave this a no-op.
        """
        ...

    def on_order_rejected(self, pair: str, signal: Signal, reason: str) -> None:
        """Notify the strategy that a requested open was NOT executed.

        The engine calls this when a ``Decision.open(signal)`` from
        :meth:`on_bar` is rejected before a position exists — e.g. a risk/limit
        gate refused it, position sizing produced an invalid lot, or the broker
        rejected the order.  ``reason`` is a short human-readable explanation.

        It is the counterpart to :meth:`on_fill`: a strategy that optimistically
        prepared state for an open can roll it back here.  The Daily Lull simply
        leaves its ``traded`` flag False (it is only set in :meth:`on_fill`), so
        a rejected entry keeps the session eligible for a later one.

        Stateless strategies may leave this a no-op.
        """
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY: dict[str, type] = {}


def register(strategy_cls: type) -> type:
    """Register *strategy_cls* in the global registry under its ``name``.

    The class must expose a non-empty ``name`` attribute that is unique across
    the registry (the name is also the config key and DB ``strategy`` value,
    D054/D056).  Returns the class unchanged so it can be used as a decorator.

    Raises
    ------
    ValueError
        If the class has no usable ``name`` or the name is already registered.
    """
    name = getattr(strategy_cls, "name", None)
    if not isinstance(name, str) or not name:
        raise ValueError(
            f"Strategy class {strategy_cls!r} must define a non-empty string 'name' to register."
        )
    if name in STRATEGY_REGISTRY:
        existing = STRATEGY_REGISTRY[name]
        raise ValueError(
            f"Strategy name {name!r} is already registered to {existing!r}; "
            f"cannot register {strategy_cls!r}."
        )
    STRATEGY_REGISTRY[name] = strategy_cls
    return strategy_cls


def get_strategy(name: str) -> type:
    """Return the strategy class registered under *name*.

    Raises
    ------
    ValueError
        If no strategy is registered under *name*, with the list of known names.
    """
    try:
        return STRATEGY_REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(STRATEGY_REGISTRY)) or "(none)"
        raise ValueError(
            f"No strategy registered under name {name!r}. Known strategies: {known}."
        ) from None
