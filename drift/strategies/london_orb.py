"""London Opening-Range Breakout — ported to the Strategy contract (D051, D067-D069).

This is strategy #2 on the multi-strategy platform.  It is a momentum/breakout
strategy built on the first hour of the London session (10:00-11:00 server time),
inverting every failure mode of the Daily Lull (#1):

  - Mean reversion → Momentum directional (breakout)
  - Illiquid fringe (23:00-02:00) → Liquid London hours (10:00-18:00)
  - TP at midpoint (reward ≤ spread) → TP = 1× range width (R:R 1:1)

Session window (MT5 server time, GMT+2/+3 — NOT real UTC; the candle timestamps
arrive labeled tz-aware but are reasoned over as the server clock):

  10:00-10:59  Range definition — accumulate high/low of each M15 bar; no entries.
  11:00        Lock — fix the range; apply ATR-width + pip-floor filters.
  11:00-17:59  Trading window — evaluate breakouts once the range is locked;
               BUY if close > range_high, SELL if close < range_low.
               Maximum one trade per pair per day.
  18:00        Time stop — force-close any open position; reset state; always emit
               the session-closed heartbeat (L4 / D066 pattern).
  Outside      NoOp.

The strategy operates Monday through Friday.  Unlike the Daily Lull it does NOT
skip Friday — the London session ends well before the weekend open risk is a
concern (see D068).

Per-pair state and parameter parsing all live here; the engine only knows when
an M15 candle closes (D051).  The engine already serves only CLOSED bars (D045),
so this module does NOT re-apply a closed-bar filter.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

import pandas as pd

from drift.indicators import atr as _atr
from drift.pricing import pip_multiplier
from drift.strategies.base import Decision, MarketData, Signal, StrategyContext, register

logger = logging.getLogger(__name__)

# Number of M15 candles to request — 150 is ample for ATR(14) Wilder warmup.
_M15_COUNT = 150

# Freshness tolerance: a served candle no older than one M15 interval is considered
# fresh (mirrors the Daily Lull's _M15_INTERVAL freshness guard for the time stop).
_M15_INTERVAL = timedelta(minutes=15)


# ---------------------------------------------------------------------------
# Parameters (parsed by the strategy itself, D054)
# ---------------------------------------------------------------------------


@dataclass
class LondonOrbParams:
    """London ORB parameters, parsed from the opaque ``params`` config dict.

    Each field maps directly to the same-named key in the ``params`` block of
    the strategy's ``config.yaml`` entry.  Missing keys fall back to the
    dataclass defaults shown below.  The engine never reads these fields; they
    are private to the strategy (D054).
    """

    range_start_hour: int = 10  # begin measuring the range (server time)
    range_end_hour: int = 11  # lock the range / start trading (server time)
    time_stop_hour: int = 18  # close all + heartbeat (server time)
    range_atr_min: float = 0.5  # minimum range width in ATR multiples
    range_atr_max: float = 2.0  # maximum range width in ATR multiples
    range_pip_floor: dict[str, float] = field(default_factory=dict)  # per-pair absolute pip floor
    tp_mult: float = 1.0  # TP = tp_mult × range_width beyond the breakout level
    atr_period: int = 14  # ATR period on M15 bars

    @classmethod
    def from_dict(cls, params: dict) -> LondonOrbParams:
        """Build :class:`LondonOrbParams` from a raw config ``params`` dict.

        Only known scalar fields are consumed; unknown keys are ignored so a newer
        config does not crash an older binary.  The ``range_pip_floor`` nested dict
        is parsed separately.  Missing keys fall back to the dataclass defaults.
        """
        scalar_fields = {k for k in cls.__dataclass_fields__ if k != "range_pip_floor"}
        kwargs: dict = {k: v for k, v in (params or {}).items() if k in scalar_fields}
        pip_floor = params.get("range_pip_floor") if params else None
        if isinstance(pip_floor, dict):
            kwargs["range_pip_floor"] = {str(k): float(v) for k, v in pip_floor.items()}
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Per-pair state (private to the strategy, D051 / framework §3)
# ---------------------------------------------------------------------------


@dataclass
class OrbSessionState:
    """Per-pair tracking across one London ORB session (10:00-18:00 server time).

    Reset on the first bar of a new calendar day (the 10:00 wake).  The
    ``session_date`` ordinal distinguishes days and drives the reset.
    """

    session_date: int | None = None  # ordinal of the current session day
    high: float = float("-inf")  # running high during range-definition
    low: float = float("inf")  # running low during range-definition
    locked: bool = False  # True after 11:00 if the range passed the filters
    tradeable: bool = False  # True when the locked range also passed pip/atr filters
    range_high: float = float("nan")  # locked high
    range_low: float = float("nan")  # locked low
    range_atr_ratio: float = float("nan")  # (range_high - range_low) / atr at lock
    atr_at_lock: float = float("nan")  # ATR value at lock time (for logging)
    traded: bool = False  # at most one trade per day per pair


# ---------------------------------------------------------------------------
# Scheduling helpers
# ---------------------------------------------------------------------------


def _next_m15_close(now: datetime) -> datetime:
    """Return the next M15 boundary strictly after *now* (HH:00/15/30/45)."""
    minute = now.minute
    next_slot = ((minute // 15) + 1) * 15
    if next_slot < 60:
        return now.replace(minute=next_slot, second=0, microsecond=0)
    next_hour = now + timedelta(hours=1)
    return next_hour.replace(minute=0, second=0, microsecond=0)


def _next_session_start(now: datetime, start_hour: int) -> datetime:
    """Return the next ``start_hour``:00 server time strictly after *now*.

    Skips Saturday and Sunday (market closed); Monday through Friday are all
    valid for the London session.  Unlike the Daily Lull, Friday is NOT skipped
    (D068): the ORB session ends at 18:00, well before the weekend risk window.
    """
    candidate = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    # Skip weekend (Saturday=5, Sunday=6)
    while candidate.weekday() in (5, 6):
        candidate += timedelta(days=1)
    return candidate


def _in_trading_window(now: datetime, start_hour: int, stop_hour: int) -> bool:
    """Return True iff *now* is inside the active ORB window [start_hour, stop_hour)."""
    h = now.hour
    return start_hour <= h < stop_hour and now.weekday() not in (5, 6)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


@register
class LondonOrbStrategy:
    """London Opening-Range Breakout as a :class:`~drift.strategies.base.Strategy`.

    Momentum breakout of the first London hour's range (10:00-11:00 server
    time).  Ticks on M15 closes only.  Per-pair :class:`OrbSessionState` is
    private to the instance.

    The engine-facing contract (D051): ``name``, ``pairs``, ``timeframes``,
    ``on_bar``, ``on_fill``, ``on_order_rejected``, ``next_wake``.
    """

    name = "london_orb"
    timeframes = frozenset({"M15"})
    params_cls = LondonOrbParams  # used by engine._instantiate to resolve params (D054)

    def __init__(self, pairs: list[str], params: LondonOrbParams) -> None:
        self.pairs = list(pairs)
        self.params = params
        self._states: dict[str, OrbSessionState] = {p: OrbSessionState() for p in self.pairs}

    # -- state access ----------------------------------------------------------

    def _state(self, pair: str) -> OrbSessionState:
        """Return the per-pair state, creating it lazily for unknown pairs."""
        state = self._states.get(pair)
        if state is None:
            state = OrbSessionState()
            self._states[pair] = state
        return state

    # -- main entry point ------------------------------------------------------

    def on_bar(
        self,
        pair: str,
        timeframe: str,
        bar_close_time: datetime,
        market: MarketData,
        ctx: StrategyContext,
    ) -> Decision:
        """Evaluate one closed M15 candle and return a :class:`Decision`.

        ``bar_close_time`` is the engine's authoritative boundary (D058), used
        exclusively for the 18:00 time-stop check (with a freshness guard, D059).
        All range-definition, lock, and trading-window logic keys on the served
        candle's own open-time hour (``bar_time.hour``), matching the Daily Lull
        convention and ensuring live/backtest parity (D055).

        Every evaluation carries a :class:`Signal` (accepted, rejected or time-stop)
        so the engine logs all signals for full transparency (D046).
        """
        state = self._state(pair)
        m15_df = market.candles(pair, "M15", _M15_COUNT)

        if len(m15_df) < 2:
            _now = datetime.now(tz=timezone.utc)
            _t = m15_df.index[-1].to_pydatetime() if len(m15_df) >= 1 else _now
            return Decision.noop(
                Signal(
                    action="none",
                    pair=pair,
                    timestamp=_t,
                    m15_candle_time=_t,
                    h4_candle_time=_t,
                    entry_price=float("nan"),
                    reason="insufficient_data",
                    rejection_reason="insufficient_data",
                )
            )

        signal = self._evaluate(pair, m15_df, state, bar_close_time)

        if signal.reason == "session_end_time_stop":
            decision = Decision.close_all("session_close")
            decision.signal = signal
            return decision

        if signal.action in ("buy", "sell"):
            return Decision.open(signal)

        return Decision.noop(signal=signal)

    # -- evaluation core -------------------------------------------------------

    def _evaluate(
        self,
        pair: str,
        m15_df: pd.DataFrame,
        state: OrbSessionState,
        bar_close_time: datetime,
    ) -> Signal:
        """Pure evaluation: update state, compute indicators, build a Signal.

        ``bar_close_time`` drives only the 18:00 time-stop (with a freshness
        guard, D058/D059).  All range-definition, lock, and trading-window logic
        keys on ``bar_time.hour`` — the served candle's own open-time hour — so
        live and backtest classify each candle identically (D055).
        """
        params = self.params

        # Compute ATR for the range filter (always needed at lock time)
        m15_high = m15_df["high"]
        m15_low = m15_df["low"]
        m15_close = m15_df["close"]
        atr_series = _atr(m15_high, m15_low, m15_close, params.atr_period)
        atr_val = float(atr_series.iloc[-1])

        bar_ts = m15_df.index[-1]
        bar_time: datetime = bar_ts.to_pydatetime()
        curr_close = float(m15_close.iloc[-1])
        curr_high = float(m15_high.iloc[-1])
        curr_low = float(m15_low.iloc[-1])

        # Candle hour / weekday: authoritative clock for range/lock/trading windows.
        candle_hour = bar_time.hour
        candle_weekday = bar_time.weekday()

        def _base_signal(
            action: Literal["buy", "sell", "none"],
            reason: str,
            rejection_reason: str | None = None,
        ) -> Signal:
            return Signal(
                action=action,
                pair=pair,
                timestamp=bar_time,
                m15_candle_time=bar_time,
                h4_candle_time=bar_time,  # ORB does not read H4; alias to bar_time
                entry_price=curr_close,
                range_high=state.range_high,
                range_low=state.range_low,
                range_atr_ratio=state.range_atr_ratio,
                atr_value=atr_val,
                reason=reason,
                rejection_reason=rejection_reason,
            )

        # --- Weekend: skip ---
        if candle_weekday in (5, 6):
            return _base_signal("none", "outside_window")

        # --- 18:00 time stop ---
        # Key off the engine boundary (bar_close_time), not the candle hour.
        # Freshness guard mirrors the Lull: only fire when the served candle is
        # within one M15 interval of the boundary, so a stale frame after a long
        # idle cannot trigger a spurious close.
        if bar_close_time.hour == params.time_stop_hour:
            if timedelta(0) <= (bar_close_time - bar_time) <= _M15_INTERVAL:
                self._reset_state(state)
                return _base_signal("none", "session_end_time_stop")
            return _base_signal("none", "stale_session_data", "stale_session_data")

        # --- Outside the active window ---
        if not (params.range_start_hour <= candle_hour < params.time_stop_hour):
            return _base_signal("none", "outside_window")

        # --- Range-definition phase: candle_hour == range_start_hour (10) ---
        # Accumulates candles opening at 10:00, 10:15, 10:30, and 10:45.
        if candle_hour == params.range_start_hour:
            self._accumulate_range(state, bar_time, curr_high, curr_low)
            return _base_signal("none", "define_range")

        # --- Lock phase: first candle opening at range_end_hour (11) ---
        # Lock the range using the already-accumulated 10:xx high/low, then fall
        # through to breakout evaluation on this same bar (mirrors the Lull: the
        # lock candle is also a valid breakout candidate).
        if candle_hour == params.range_end_hour and not state.locked:
            self._lock_range(state, pair, atr_val, params)
            if not state.tradeable:
                return _base_signal("none", "range_rejected")

        # --- Trading phase: candle_hour in [range_end_hour, time_stop_hour) ---
        if not state.locked:
            return _base_signal("none", "range_not_locked", "range_not_locked")

        if not state.tradeable:
            return _base_signal("none", "range_not_tradeable", "range_not_tradeable")

        if state.traded:
            return _base_signal(
                "none", "already_traded_this_session", "already_traded_this_session"
            )

        range_high = state.range_high
        range_low = state.range_low
        range_width = range_high - range_low

        # BUY breakout: close above range_high
        if curr_close > range_high:
            sl = range_low
            tp = range_high + params.tp_mult * range_width
            reason = f"orb_buy breakout={curr_close:.5f} range=[{range_low:.5f},{range_high:.5f}]"
            logger.info(
                "%s | action=buy entry=%.5f sl=%.5f tp=%.5f range_width=%.5f",
                pair,
                curr_close,
                sl,
                tp,
                range_width,
            )
            sig = _base_signal("buy", reason)
            sig.sl = sl
            sig.tp = tp
            return sig

        # SELL breakout: close below range_low
        if curr_close < range_low:
            sl = range_high
            tp = range_low - params.tp_mult * range_width
            reason = f"orb_sell breakout={curr_close:.5f} range=[{range_low:.5f},{range_high:.5f}]"
            logger.info(
                "%s | action=sell entry=%.5f sl=%.5f tp=%.5f range_width=%.5f",
                pair,
                curr_close,
                sl,
                tp,
                range_width,
            )
            sig = _base_signal("sell", reason)
            sig.sl = sl
            sig.tp = tp
            return sig

        # No breakout yet
        return _base_signal(
            "none",
            f"no_breakout close={curr_close:.5f} range=[{range_low:.5f},{range_high:.5f}]",
        )

    # -- state helpers ---------------------------------------------------------

    def _accumulate_range(
        self,
        state: OrbSessionState,
        bar_time: datetime,
        bar_high: float,
        bar_low: float,
    ) -> None:
        """Accumulate high/low during the range-definition window.

        Resets state at the start of a new session day (keyed by calendar date).
        """
        day_ordinal = bar_time.toordinal()
        if state.session_date != day_ordinal:
            # New day — reset state for this session
            state.session_date = day_ordinal
            state.high = float("-inf")
            state.low = float("inf")
            state.locked = False
            state.tradeable = False
            state.range_high = float("nan")
            state.range_low = float("nan")
            state.range_atr_ratio = float("nan")
            state.atr_at_lock = float("nan")
            state.traded = False
            logger.debug("London ORB new session — day_ordinal=%d", day_ordinal)

        if bar_high > state.high:
            state.high = bar_high
        if bar_low < state.low:
            state.low = bar_low

    def _lock_range(
        self,
        state: OrbSessionState,
        pair: str,
        atr_val: float,
        params: LondonOrbParams,
    ) -> None:
        """Lock the range at 11:00 and apply the ATR-width + pip-floor filters.

        Sets ``locked=True`` always (prevents re-locking on subsequent 11:xx bars)
        and ``tradeable=True`` only when both filters pass.  Logs the outcome.
        """
        state.locked = True  # prevents re-entry on 11:15, 11:30, etc.

        if state.high == float("-inf") or state.low == float("inf"):
            logger.debug("%s | ORB range empty at lock — no define-range bars seen", pair)
            state.tradeable = False
            return

        range_width = state.high - state.low
        state.range_high = state.high
        state.range_low = state.low

        if math.isnan(atr_val) or atr_val <= 0:
            logger.debug("%s | ORB range lock — ATR not ready (%.5f)", pair, atr_val)
            state.tradeable = False
            return

        ratio = range_width / atr_val
        state.range_atr_ratio = ratio
        state.atr_at_lock = atr_val

        # ATR-width filter
        if not (params.range_atr_min <= ratio <= params.range_atr_max):
            logger.debug(
                "%s | ORB range rejected (atr filter) — width=%.5f atr=%.5f ratio=%.2f "
                "min=%.1f max=%.1f",
                pair,
                range_width,
                atr_val,
                ratio,
                params.range_atr_min,
                params.range_atr_max,
            )
            state.tradeable = False
            return

        # Pip-floor filter
        pip_mult = pip_multiplier(pair)
        range_pips = range_width * pip_mult
        floor_pips = params.range_pip_floor.get(pair, 0.0)
        if range_pips < floor_pips:
            logger.debug(
                "%s | ORB range rejected (pip floor) — width_pips=%.1f floor=%.1f",
                pair,
                range_pips,
                floor_pips,
            )
            state.tradeable = False
            return

        state.tradeable = True
        logger.debug(
            "%s | ORB range locked — high=%.5f low=%.5f width=%.5f atr=%.5f ratio=%.2f pips=%.1f",
            pair,
            state.range_high,
            state.range_low,
            range_width,
            atr_val,
            ratio,
            range_pips,
        )

    @staticmethod
    def _reset_state(state: OrbSessionState) -> None:
        """Reset per-pair state at the 18:00 time stop for the next session."""
        state.session_date = None
        state.high = float("-inf")
        state.low = float("inf")
        state.locked = False
        state.tradeable = False
        state.range_high = float("nan")
        state.range_low = float("nan")
        state.range_atr_ratio = float("nan")
        state.atr_at_lock = float("nan")
        state.traded = False

    # -- lifecycle callbacks ---------------------------------------------------

    def on_fill(self, pair: str, signal: Signal, ticket: int) -> None:
        """Mark the session as traded once the engine confirms the open.

        The ORB allows at most one trade per pair per day.  ``traded`` is set
        here (not in ``on_bar``) so it flips True only when the trade was
        actually opened — exactly like the Daily Lull pattern.
        """
        self._state(pair).traded = True

    def on_order_rejected(self, pair: str, signal: Signal, reason: str) -> None:
        """Leave ``traded`` False when the engine rejects a requested open.

        Because ``on_bar`` does not pre-set ``traded``, a rejected open needs no
        rollback: the session stays eligible for the next breakout candle.
        """

    # -- scheduling hint (D058) ------------------------------------------------

    def next_wake(self, now: datetime) -> datetime | None:
        """Return the next M15-close boundary this strategy needs to evaluate.

        Inside the active window (Mon-Fri 10:00-18:00 server time) the next wake
        is the next M15 close.  Outside it, the next weekday 10:00 server time.
        Both boundaries carry no broker delay — the engine adds that on top (D058).
        """
        params = self.params
        if _in_trading_window(now, params.range_start_hour, params.time_stop_hour):
            return _next_m15_close(now)
        return _next_session_start(now, params.range_start_hour)
