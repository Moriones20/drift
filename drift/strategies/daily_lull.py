"""Daily Lull Scalper — ported to the Strategy contract (D051, Step 29).

This is the reference strategy (#1) on the multi-strategy platform.  It is a
straight port of the live logic in ``drift/strategy.py`` (``SessionState``,
``update_session_state``, ``should_close_on_time``, ``evaluate_pair``) onto the
``Strategy`` protocol in ``drift/strategies/base.py``.  The behavior is
intentionally identical — equivalence is guarded by
``tests/test_daily_lull_strategy.py`` — so the strategy can be adopted live on
its existing demo positions (magic 234000) without surprises.

Session window (MT5 server time, GMT+2/+3 — NOT real UTC; the candle timestamps
arrive labeled tz-aware but are reasoned over as the server clock):

  21:00-22:59  Range definition — accumulate high/low, no entries.
  23:00        Lock — fix the range if the ATR-width filter passes.
  23:00-01:59  Trading window — evaluate entries once the range is locked.
  02:00        Time stop — force-close any open position, reset state.
  02:01-20:59  Outside window — skip.

The session window logic, per-pair state and parameter parsing all live here;
the engine only knows when an M15 candle closes (D051).  The engine already
serves only CLOSED bars (D045), so this module does NOT re-apply a closed-bar
filter — it reads candles straight from ``market.candles`` and acts on
``iloc[-1]``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import pandas as pd

from drift.indicators import adx as _adx
from drift.indicators import atr as _atr
from drift.indicators import rsi as _rsi
from drift.strategies.base import Decision, MarketData, Signal, StrategyContext, register

logger = logging.getLogger(__name__)

# Number of candles to request from the engine.  Mirrors main.py's live fetch
# (M15 count=150 for ATR/RSI warmup, H4 count=50 for ADX warmup).
_M15_COUNT = 150
_H4_COUNT = 50


# ---------------------------------------------------------------------------
# Parameters (parsed by the strategy itself, D054)
# ---------------------------------------------------------------------------


@dataclass
class DailyLullParams:
    """Daily Lull parameters, parsed from the opaque ``params`` config dict.

    Defaults match ``drift.config.StrategyConfig`` exactly so a config that
    omits a field behaves identically to the legacy single-strategy config.
    The engine never reads these fields; they are private to the strategy
    (D054).
    """

    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    adx_max_threshold: float = 35.0
    session_start_hour: int = 21
    range_definition_hours: int = 2
    session_end_hour: int = 2
    range_atr_min: float = 1.0
    range_atr_max: float = 4.0
    sl_atr_mult: float = 2.5
    m15_rsi_period: int = 14
    m15_atr_period: int = 14
    h4_adx_period: int = 14

    @classmethod
    def from_dict(cls, params: dict) -> DailyLullParams:
        """Build :class:`DailyLullParams` from a raw config ``params`` dict.

        Only known fields are consumed; unknown keys are ignored so a newer
        config does not crash an older binary.  Missing keys fall back to the
        dataclass defaults (which mirror ``StrategyConfig``).
        """
        known = set(cls.__dataclass_fields__)
        kwargs = {k: v for k, v in (params or {}).items() if k in known}
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Per-pair state (private to the strategy, D051 / framework §3)
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    """Per-pair tracking across one Daily Lull session (21:00->02:00 next day, server time).

    Moved verbatim from ``drift/strategy.py``; it is now private state owned by
    :class:`DailyLullStrategy` (``self._states``) rather than living loose in
    ``main.py``.
    """

    session_date: int | None = None  # ordinal of session-start day (server time)
    high: float = float("-inf")  # running high during 21:00-22:59
    low: float = float("inf")  # running low during 21:00-22:59
    locked: bool = False  # True after 23:00 if range is valid
    range_high: float = float("nan")  # locked value
    range_low: float = float("nan")  # locked value
    traded: bool = False  # at most one trade per session per pair


def _session_key(bar_time: datetime) -> int:
    """Return an integer identifying the session this bar belongs to.

    Sessions start at 21:00 server time.  Bars at 21:00-23:59 belong to the
    session of that calendar day.  Bars at 00:00-01:59 belong to the session
    that started the previous calendar day (carry-over).
    """
    if bar_time.hour < 2:
        # Carry-over: shift back to previous day's ordinal
        shifted = bar_time - pd.Timedelta(hours=3)
        return shifted.toordinal()
    return bar_time.toordinal()


def _update_session_state(
    state: SessionState,
    bar_time: datetime,
    bar_high: float,
    bar_low: float,
    atr: float,
    params: DailyLullParams,
) -> None:
    """Update *state* in-place for the range-definition window (21:00-22:59).

    Also locks the range at 23:00 when the ATR-width filter passes.  Mirrors
    ``drift.strategy.update_session_state`` exactly.
    """
    hour = bar_time.hour
    key = _session_key(bar_time)

    # Detect new session start at 21:00
    if hour == 21 and state.session_date != key:
        state.session_date = key
        state.high = float("-inf")
        state.low = float("inf")
        state.locked = False
        state.range_high = float("nan")
        state.range_low = float("nan")
        state.traded = False
        logger.debug("New session started — session_key=%d", key)

    # Accumulate range during 21:00-22:59
    if state.session_date == key and hour in (21, 22):
        if bar_high > state.high:
            state.high = bar_high
        if bar_low < state.low:
            state.low = bar_low

    # Lock range at 23:00 (only once per session)
    if (
        state.session_date == key
        and hour == 23
        and not state.locked
        and state.high > float("-inf")
        and state.low < float("inf")
    ):
        if not math.isnan(atr) and atr > 0:
            range_width = state.high - state.low
            in_range = params.range_atr_min * atr <= range_width <= params.range_atr_max * atr
            if in_range:
                state.range_high = state.high
                state.range_low = state.low
                state.locked = True
                logger.debug(
                    "Session range locked — high=%.5f low=%.5f width=%.5f atr=%.5f",
                    state.range_high,
                    state.range_low,
                    range_width,
                    atr,
                )
            else:
                # Range outside acceptable ATR bounds — skip this session
                state.locked = False
                logger.debug(
                    "Session range rejected — width=%.5f atr=%.5f min=%.1fx max=%.1fx",
                    range_width,
                    atr,
                    params.range_atr_min,
                    params.range_atr_max,
                )


# ---------------------------------------------------------------------------
# Scheduling helpers (ported from main.py for Step 32a; equivalence guarded by
# tests/test_daily_lull_next_wake.py)
# ---------------------------------------------------------------------------


def _next_m15_close(now: datetime) -> datetime:
    """Return the next M15 boundary strictly after *now* (HH:00, HH:15, HH:30, HH:45).

    *now* must be in MT5 server time so the result aligns with candle close
    timestamps.  Ported verbatim from ``main._next_m15_close``.
    """
    minute = now.minute
    next_slot = ((minute // 15) + 1) * 15
    if next_slot < 60:
        return now.replace(minute=next_slot, second=0, microsecond=0)
    next_hour = now + timedelta(hours=1)
    return next_hour.replace(minute=0, second=0, microsecond=0)


def _in_session_window(now: datetime, start_hour: int, end_hour: int) -> bool:
    """Return True iff *now* (MT5 server time) is inside an active Daily Lull session.

    A session spans ``start_hour`` (21) on day N to ``end_hour`` (2) on day N+1,
    identified by its start day: sessions starting Mon-Thu and Sun are valid;
    sessions starting Fri (Sydney close leaves less than the 2-hour range window)
    and Sat (market closed) are skipped.  So Friday 00:00-01:59 server time is
    allowed because it is the tail of Thursday's session.

    Ported verbatim from ``main._in_session_window`` but parameterized on the
    strategy's own ``start_hour``/``end_hour`` instead of reading global config.
    """
    h = now.hour
    if h >= start_hour:
        # Tonight is the start of "today's" session — valid unless Fri or Sat.
        return now.weekday() not in (4, 5)
    if h < end_hour:
        # Tail of "yesterday's" session — valid unless yesterday was Fri or Sat.
        yesterday_weekday = (now - timedelta(days=1)).weekday()
        return yesterday_weekday not in (4, 5)
    return False


def _next_session_start(now: datetime, start_hour: int) -> datetime:
    """Return the next ``start_hour``:00 server time strictly after *now*.

    Skips Friday and Saturday session starts (no market / not enough window);
    Sunday 21:00 server time IS a valid session start (Sydney open of the new
    week).  Ported verbatim from ``main._next_session_start`` but parameterized
    on the strategy's own ``start_hour``.
    """
    candidate = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() in (4, 5):
        candidate += timedelta(days=1)
    return candidate


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class DailyLullStrategy:
    """Daily Lull Scalper as a :class:`~drift.strategies.base.Strategy`.

    Mean reversion during the daily lull (21:00-02:00 server time) over a range
    fixed in the first two hours.  Ticks on M15 closes; reads H4 as context for
    the ADX regime filter.  Per-pair :class:`SessionState` is private to the
    instance.
    """

    name = "daily_lull"
    timeframes = frozenset({"M15"})

    def __init__(self, pairs: list[str], params: DailyLullParams) -> None:
        self.pairs = list(pairs)
        self.params = params
        self._states: dict[str, SessionState] = {p: SessionState() for p in self.pairs}

    # -- state access ------------------------------------------------------

    def _state(self, pair: str) -> SessionState:
        """Return the per-pair state, creating it lazily for unknown pairs."""
        state = self._states.get(pair)
        if state is None:
            state = SessionState()
            self._states[pair] = state
        return state

    # -- main entry point --------------------------------------------------

    def on_bar(
        self,
        pair: str,
        timeframe: str,
        bar_close_time: datetime,
        market: MarketData,
        ctx: StrategyContext,
    ) -> Decision:
        """Evaluate one closed M15 candle and return a :class:`Decision`.

        Replicates ``drift.strategy.evaluate_pair`` (entry logic, range update,
        time stop) over the per-pair :class:`SessionState`.  Every evaluation —
        accepted entry, rejection, or time stop — carries a :class:`Signal` so
        the engine logs all signals (transparency principle, D046).
        """
        state = self._state(pair)

        m15_df = market.candles(pair, "M15", _M15_COUNT)
        h4_df = market.candles(pair, "H4", _H4_COUNT)

        signal = self._evaluate(pair, m15_df, h4_df, state)

        # Time stop at session_end_hour (02:00): close every position of this
        # strategy.  Carry the signal so the rejection-style log entry survives
        # (mirrors evaluate_pair returning a "session_end_time_stop" signal).
        if signal.reason == "session_end_time_stop":
            decision = Decision.close_all("session_close")
            decision.signal = signal
            return decision

        if signal.action in ("buy", "sell"):
            # NOTE: traded is set in on_fill, not here — the engine confirms the
            # open before we mark the session as used (see on_fill).
            return Decision.open(signal)

        # Any "none" / rejection: noop but carry the signal for transparent logging.
        return Decision.noop(signal=signal)

    # -- evaluation core (mirror of evaluate_pair) -------------------------

    def _evaluate(
        self,
        symbol: str,
        m15_df: pd.DataFrame,
        h4_df: pd.DataFrame,
        state: SessionState,
    ) -> Signal:
        """Pure evaluation: compute indicators, update state, build a Signal.

        Identical logic to ``drift.strategy.evaluate_pair``, but it never sets
        ``state.traded`` (that moves to :meth:`on_fill`).
        """
        params = self.params

        if len(m15_df) < 2 or len(h4_df) < 2:
            from datetime import timezone  # local import to avoid top-level DTZ003 noise

            _now = datetime.now(tz=timezone.utc)
            _m15_t = m15_df.index[-1].to_pydatetime() if len(m15_df) >= 1 else _now
            _h4_t = h4_df.index[-1].to_pydatetime() if len(h4_df) >= 1 else _now
            return Signal(
                action="none",
                pair=symbol,
                timestamp=_m15_t,
                m15_candle_time=_m15_t,
                h4_candle_time=_h4_t,
                entry_price=float("nan"),
                reason="insufficient_data",
                rejection_reason="insufficient_data",
            )

        # --- Compute indicators ---
        m15_close = m15_df["close"]
        m15_high = m15_df["high"]
        m15_low = m15_df["low"]

        rsi_series = _rsi(m15_close, params.m15_rsi_period)
        atr_series = _atr(m15_high, m15_low, m15_close, params.m15_atr_period)

        # H4 ADX: shift by 1 to prevent look-ahead bias (mirrors prepare_lull_data)
        h4_adx_series = _adx(h4_df["high"], h4_df["low"], h4_df["close"], params.h4_adx_period)
        h4_adx_shifted = h4_adx_series.shift(1)

        # Current bar values
        atr_val = float(atr_series.iloc[-1])
        rsi_val = float(rsi_series.iloc[-1])

        # Propagate shifted H4 ADX forward-filled to the current M15 bar timestamp
        bar_ts = m15_df.index[-1]
        h4_adx_resampled = h4_adx_shifted.resample("15min").last().ffill()
        _valid_idx = h4_adx_resampled.index[h4_adx_resampled.index <= bar_ts]
        h4_adx_at_bar = h4_adx_resampled.reindex(_valid_idx)
        adx_val = float(h4_adx_at_bar.iloc[-1]) if len(h4_adx_at_bar) > 0 else float("nan")

        bar_time: datetime = bar_ts.to_pydatetime()
        h4_candle_time: datetime = h4_df.index[-1].to_pydatetime()

        curr_close = float(m15_df["close"].iloc[-1])
        curr_high = float(m15_df["high"].iloc[-1])
        curr_low = float(m15_df["low"].iloc[-1])

        def _base_signal(
            action: Literal["buy", "sell", "none"],
            reason: str,
            rejection_reason: str | None = None,
        ) -> Signal:
            range_width = state.range_high - state.range_low if state.locked else float("nan")
            ratio = (
                range_width / atr_val
                if (not math.isnan(range_width) and atr_val > 0)
                else float("nan")
            )
            return Signal(
                action=action,
                pair=symbol,
                timestamp=bar_time,
                m15_candle_time=bar_time,
                h4_candle_time=h4_candle_time,
                entry_price=curr_close,
                range_high=state.range_high,
                range_low=state.range_low,
                range_atr_ratio=ratio,
                rsi=rsi_val,
                atr_value=atr_val,
                h4_adx=adx_val,
                reason=reason,
                rejection_reason=rejection_reason,
            )

        # --- Guard: valid ATR required ---
        if math.isnan(atr_val) or atr_val <= 0:
            return _base_signal("none", "atr_not_ready", "atr_not_ready")

        # --- Guard: valid RSI and ADX required ---
        if math.isnan(rsi_val) or math.isnan(adx_val):
            return _base_signal("none", "indicators_not_ready", "indicators_not_ready")

        hour = bar_time.hour

        # --- Update session state (range definition + locking) ---
        _update_session_state(
            state=state,
            bar_time=bar_time,
            bar_high=curr_high,
            bar_low=curr_low,
            atr=atr_val,
            params=params,
        )

        # --- Time stop: signal close at session_end_hour ---
        if bar_time.hour == params.session_end_hour:
            return _base_signal("none", "session_end_time_stop")

        # --- Entry logic: only between 23:00-01:59 with a locked range ---

        # Outside trading window
        if hour not in (23, 0, 1):
            return _base_signal("none", "outside_window")

        # Range must be locked
        if not state.locked:
            return _base_signal("none", "range_not_locked", "range_not_locked")

        # Already traded this session
        if state.traded:
            return _base_signal(
                "none", "already_traded_this_session", "already_traded_this_session"
            )

        # H4 ADX regime filter
        if adx_val >= params.adx_max_threshold:
            reason = f"adx_trending adx={adx_val:.1f} threshold={params.adx_max_threshold}"
            logger.info("%s | action=none reason=%s", symbol, reason)
            return _base_signal("none", reason, "adx_trending")

        range_high = state.range_high
        range_low = state.range_low
        sl_dist = atr_val * params.sl_atr_mult
        mid = (range_high + range_low) / 2.0

        # BUY: price at/below session low AND RSI oversold
        if curr_close <= range_low and rsi_val < params.rsi_oversold:
            sl = curr_close - sl_dist
            tp = mid
            reason = (
                f"lull_scalper_buy range_low={range_low:.5f} rsi={rsi_val:.1f} adx={adx_val:.1f}"
            )
            logger.info(
                "%s | action=buy entry=%.5f sl=%.5f tp=%.5f rsi=%.1f adx=%.1f",
                symbol,
                curr_close,
                sl,
                tp,
                rsi_val,
                adx_val,
            )
            sig = _base_signal("buy", reason)
            sig.sl = sl
            sig.tp = tp
            return sig

        # SELL: price at/above session high AND RSI overbought
        if curr_close >= range_high and rsi_val > params.rsi_overbought:
            sl = curr_close + sl_dist
            tp = mid
            reason = (
                f"lull_scalper_sell range_high={range_high:.5f} rsi={rsi_val:.1f} adx={adx_val:.1f}"
            )
            logger.info(
                "%s | action=sell entry=%.5f sl=%.5f tp=%.5f rsi=%.1f adx=%.1f",
                symbol,
                curr_close,
                sl,
                tp,
                rsi_val,
                adx_val,
            )
            sig = _base_signal("sell", reason)
            sig.sl = sl
            sig.tp = tp
            return sig

        # No entry condition met
        return _base_signal(
            "none",
            f"no_entry_condition close={curr_close:.5f} range=[{range_low:.5f},{range_high:.5f}]"
            f" rsi={rsi_val:.1f}",
        )

    # -- lifecycle callbacks (contract extension, D051 / Step 29) ----------

    def on_fill(self, pair: str, signal: Signal, ticket: int) -> None:
        """Mark the session as traded once the engine confirms the open.

        The Lull allows at most one trade per session per pair.  ``traded`` is
        set HERE (not in :meth:`on_bar`) so it flips True only when the trade was
        actually opened — matching the legacy optimistic-set-plus-rollback in
        ``main.py`` but without the engine touching private state.
        """
        self._state(pair).traded = True

    def on_order_rejected(self, pair: str, signal: Signal, reason: str) -> None:
        """Leave ``traded`` False when the engine rejects a requested open.

        Because :meth:`on_bar` no longer pre-sets ``traded``, a rejected open
        needs no rollback: the session stays eligible for a later entry, exactly
        like the legacy ``session_states[pair].traded = False`` path.  This is a
        deliberate no-op (nothing to undo).
        """

    # -- scheduling hint (contract extension, D058 / Step 32a) -------------

    def next_wake(self, now: datetime) -> datetime | None:
        """Return the next M15-close boundary the Lull needs to evaluate.

        Ports the main-loop scheduling (D058): inside an active session window
        the next wake is the next M15 close; outside it, the next session start.
        Both boundaries are in MT5 server time and carry no broker delay — the
        engine adds the post-close / rollover-settle delay on top (D044).

        The window and the skip-Friday/Saturday rule are strategy-specific (D051)
        and use only this instance's ``session_start_hour`` / ``session_end_hour``
        params, never global config.
        """
        start_hour = self.params.session_start_hour
        end_hour = self.params.session_end_hour
        if _in_session_window(now, start_hour, end_hour):
            return _next_m15_close(now)
        return _next_session_start(now, start_hour)


register(DailyLullStrategy)
