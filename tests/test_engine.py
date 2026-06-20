"""Tests for the generic multi-strategy engine (Step 32b, drift/engine.py).

Covers:
  - EngineMarketData per-tick dedup + closed-bar filter (counted fake fetch).
  - The next_wake scheduler: min boundary across strategies, and the post-close
    delay (normal 5s vs rollover-settle at 00:00).
  - Dispatch: on_bar called and each Decision routed (open / noop / close_all)
    with executor/db mocked, plus per-strategy magic attribution and the
    on_fill / on_order_rejected lifecycle hooks.

No real MT5 or DB is touched; the engine's collaborators are patched.
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from drift import engine as engine_mod  # noqa: E402
from drift.engine import Engine, EngineMarketData  # noqa: E402
from drift.strategies.base import Decision, Signal, StrategyContext  # noqa: E402

_SERVER_TZ = timezone.utc


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _candles(n: int, start: datetime, freq: str = "15min") -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=n, freq=freq, tz=_SERVER_TZ)
    return pd.DataFrame(
        {
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.0,
            "volume": 1,
        },
        index=idx,
    )


def _signal(action: str = "buy", pair: str = "EURCHF") -> Signal:
    now = datetime(2026, 6, 1, 23, 0, tzinfo=_SERVER_TZ)
    sig = Signal(
        action=action,
        pair=pair,
        timestamp=now,
        m15_candle_time=now,
        h4_candle_time=now,
        entry_price=1.0,
    )
    sig.sl = 0.99
    sig.tp = 1.01
    sig.range_low = 0.995
    sig.range_high = 1.005
    return sig


class FakeStrategy:
    """Minimal Strategy: returns a scripted Decision and records hook calls."""

    def __init__(
        self,
        name: str = "fake",
        pairs: list[str] | None = None,
        decision: Decision | None = None,
        wake: datetime | None = None,
    ) -> None:
        self.name = name
        self.pairs = pairs or ["EURCHF"]
        self.timeframes = frozenset({"M15"})
        self._decision = decision or Decision.noop()
        self._wake = wake
        self.on_bar_calls: list[tuple] = []
        self.filled: list[tuple] = []
        self.rejected: list[tuple] = []

    def on_bar(self, pair, timeframe, bar_close_time, market, ctx) -> Decision:
        self.on_bar_calls.append((pair, timeframe, bar_close_time, ctx))
        return self._decision

    def on_fill(self, pair, signal, ticket) -> None:
        self.filled.append((pair, signal, ticket))

    def on_order_rejected(self, pair, signal, reason) -> None:
        self.rejected.append((pair, signal, reason))

    def next_wake(self, now):
        return self._wake


def _make_engine(strategies, *, server_offset=timedelta(hours=3), magics=None):
    """Build an Engine with hosted strategies injected directly (skip config build)."""
    config = SimpleNamespace(
        system=SimpleNamespace(
            magic_number=234000,
            rollover_settle_seconds=150,
            order_retry_attempts=3,
            order_retry_delay_seconds=25.0,
            min_reward_fraction=0.5,
        ),
        risk_global=SimpleNamespace(
            max_open_trades=4, max_drawdown_percent=10.0, max_same_currency_direction=2
        ),
        telegram=SimpleNamespace(chat_id="123"),
        strategies={},
    )
    state = SimpleNamespace(stop_requested=False, paused=False)

    eng = Engine.__new__(Engine)
    eng.config = config
    eng.state = state
    eng.peak_balance_ref = [1000.0]
    eng.bot_app = SimpleNamespace(bot=SimpleNamespace())
    eng.server_offset = server_offset
    eng._trade_lock = threading.Lock()
    eng.shutdown_event = threading.Event()
    eng._fire_and_forget = lambda coro: None
    eng._notify_trade_opened = lambda *a, **k: None
    eng._notify_trade_closed = lambda *a, **k: None
    eng._notify_bot_status = lambda *a, **k: None
    eng._notify_error = lambda *a, **k: None

    magics = magics or {}
    eng.strategies = [
        engine_mod._HostedStrategy(
            instance=s,
            name=s.name,
            magic=magics.get(s.name, 234000),
            allocation_pct=100.0,
            risk=SimpleNamespace(
                percent_per_trade=1.0, max_open_trades=4, max_drawdown_percent=10.0
            ),
        )
        for s in strategies
    ]
    return eng


# ---------------------------------------------------------------------------
# EngineMarketData
# ---------------------------------------------------------------------------


def test_market_data_dedup_one_fetch_per_pair_timeframe():
    boundary = datetime(2026, 6, 1, 23, 0, tzinfo=_SERVER_TZ)
    df = _candles(10, datetime(2026, 6, 1, 20, 0, tzinfo=_SERVER_TZ))

    calls = {"n": 0}

    def fake_get_candles(pair, timeframe, count):
        calls["n"] += 1
        return df

    with mock.patch.object(engine_mod, "get_candles", side_effect=fake_get_candles):
        market = EngineMarketData(boundary)
        market.candles("EURCHF", "M15", 150)
        market.candles("EURCHF", "M15", 150)
        market.candles("EURCHF", "M15", 150)

    assert calls["n"] == 1  # deduplicated across repeated calls in the tick


def test_market_data_filters_to_closed_bars():
    boundary = datetime(2026, 6, 1, 23, 0, tzinfo=_SERVER_TZ)
    # Bars at 22:30, 22:45, 23:00 — the 23:00 bar is the still-forming one.
    df = _candles(3, datetime(2026, 6, 1, 22, 30, tzinfo=_SERVER_TZ))

    with mock.patch.object(engine_mod, "get_candles", return_value=df):
        market = EngineMarketData(boundary)
        out = market.candles("EURCHF", "M15", 150)

    assert out.index.max() < boundary
    assert len(out) == 2  # 22:30 and 22:45 only; 23:00 excluded


def test_market_data_reset_clears_cache():
    boundary = datetime(2026, 6, 1, 23, 0, tzinfo=_SERVER_TZ)
    df = _candles(5, datetime(2026, 6, 1, 21, 0, tzinfo=_SERVER_TZ))
    calls = {"n": 0}

    def fake_get_candles(pair, timeframe, count):
        calls["n"] += 1
        return df

    with mock.patch.object(engine_mod, "get_candles", side_effect=fake_get_candles):
        market = EngineMarketData(boundary)
        market.candles("EURCHF", "M15", 150)
        market.reset()
        market.candles("EURCHF", "M15", 150)

    assert calls["n"] == 2  # cache cleared -> fetched again


# ---------------------------------------------------------------------------
# Scheduler: min next_wake + post-close delay
# ---------------------------------------------------------------------------


def test_next_boundary_picks_minimum_across_strategies():
    early = datetime(2026, 6, 1, 23, 0, tzinfo=_SERVER_TZ)
    late = datetime(2026, 6, 1, 23, 15, tzinfo=_SERVER_TZ)
    a = FakeStrategy(name="a", wake=late)
    b = FakeStrategy(name="b", wake=early)
    eng = _make_engine([a, b])

    now = datetime(2026, 6, 1, 22, 50, tzinfo=_SERVER_TZ)
    assert eng._next_boundary(now) == early


def test_next_boundary_none_when_all_sleeping():
    a = FakeStrategy(name="a", wake=None)
    eng = _make_engine([a])
    now = datetime(2026, 6, 1, 12, 0, tzinfo=_SERVER_TZ)
    assert eng._next_boundary(now) is None


def test_post_close_delay_normal_vs_rollover():
    eng = _make_engine([FakeStrategy()])
    rollover = datetime(2026, 6, 2, 0, 0, tzinfo=_SERVER_TZ)
    normal = datetime(2026, 6, 1, 23, 15, tzinfo=_SERVER_TZ)
    assert eng._post_close_delay_seconds(rollover) == 150  # rollover_settle_seconds
    assert eng._post_close_delay_seconds(normal) == engine_mod.CANDLE_CLOSE_DELAY_SECONDS


def test_paused_strategy_stays_scheduled(caplog):
    """A paused strategy is still scheduled so closes can fire (D064)."""
    wake = datetime(2026, 6, 1, 23, 0, tzinfo=_SERVER_TZ)
    a = FakeStrategy(name="a", wake=wake)
    eng = _make_engine([a])
    eng.strategies[0].paused = True
    now = datetime(2026, 6, 1, 22, 50, tzinfo=_SERVER_TZ)
    # Paused strategy must still return a boundary — not None.
    assert eng._next_boundary(now) == wake


# ---------------------------------------------------------------------------
# Dispatch + Decision routing
# ---------------------------------------------------------------------------


def _patch_dispatch_env():
    """Patch the engine's balance/equity/positions/db for a dispatch test."""
    patches = [
        mock.patch.object(engine_mod, "get_balance", return_value=1000.0),
        mock.patch.object(engine_mod, "get_equity", return_value=1000.0),
        mock.patch.object(engine_mod, "get_open_positions", return_value=[]),
        mock.patch.object(
            engine_mod, "server_now", return_value=datetime(2026, 6, 1, 23, 0, 5, tzinfo=_SERVER_TZ)
        ),
        mock.patch.object(engine_mod, "log_peak_balance"),
    ]
    return patches


def test_dispatch_calls_on_bar_for_due_strategy():
    boundary = datetime(2026, 6, 1, 23, 0, tzinfo=_SERVER_TZ)
    strat = FakeStrategy(name="a", wake=boundary, decision=Decision.noop())
    eng = _make_engine([strat])
    due = eng.strategies  # the strategy is due at the boundary

    with (
        mock.patch.object(engine_mod, "EngineMarketData"),
        mock.patch.object(engine_mod, "get_connection"),
    ):
        for p in _patch_dispatch_env():
            p.start()
        try:
            eng._dispatch(boundary, due)
        finally:
            mock.patch.stopall()

    assert len(strat.on_bar_calls) == 1
    pair, tf, bct, ctx = strat.on_bar_calls[0]
    assert pair == "EURCHF"
    assert tf == "M15"
    assert bct == boundary
    assert isinstance(ctx, StrategyContext)


def test_due_strategies_selects_only_matching_boundary():
    """Only strategies whose next_wake equals the min boundary are due."""
    early = datetime(2026, 6, 1, 23, 0, tzinfo=_SERVER_TZ)
    late = datetime(2026, 6, 1, 23, 15, tzinfo=_SERVER_TZ)
    a = FakeStrategy(name="a", wake=early)
    b = FakeStrategy(name="b", wake=late)
    eng = _make_engine([a, b])

    now = datetime(2026, 6, 1, 22, 50, tzinfo=_SERVER_TZ)
    boundary, due = eng._due_strategies(now)

    assert boundary == early
    assert [h.name for h in due] == ["a"]  # b wakes later, not due now


def test_noop_with_signal_is_logged():
    eng = _make_engine([FakeStrategy()])
    hosted = eng.strategies[0]
    sig = _signal(action="none")
    decision = Decision.noop(signal=sig)

    with (
        mock.patch.object(engine_mod, "get_connection") as gc,
        mock.patch.object(engine_mod, "log_signal") as log_sig,
    ):
        gc.return_value.__enter__.return_value = mock.MagicMock()
        eng._handle_noop(hosted, decision)

    assert log_sig.called
    assert log_sig.call_args.kwargs["strategy"] == hosted.name


def test_open_routes_to_open_trade_with_effective_magic():
    sig = _signal(action="buy")
    strat = FakeStrategy(name="lull", decision=Decision.open(sig))
    eng = _make_engine([strat], magics={"lull": 234007})
    hosted = eng.strategies[0]

    fake_mt5 = mock.MagicMock()
    fake_mt5.symbol_info.return_value = SimpleNamespace(
        digits=5, volume_step=0.01, volume_min=0.01, volume_max=100.0, trade_tick_value=1.0
    )
    fake_mt5.positions_get.return_value = [SimpleNamespace(price_open=1.0)]

    with (
        mock.patch.object(engine_mod, "get_connection") as gc,
        mock.patch.object(engine_mod, "check_drawdown", return_value=(True, "")),
        mock.patch.object(eng, "_check_strategy_drawdown", return_value=(True, "", 1000.0, 1000.0)),
        mock.patch.object(engine_mod, "check_strategy_risk", return_value=(True, "")),
        mock.patch.object(engine_mod, "get_open_positions", return_value=[]),
        mock.patch.object(engine_mod, "get_open_trades", return_value=[]),
        mock.patch.object(engine_mod, "calculate_position_size_allocated", return_value=0.05),
        mock.patch.object(engine_mod, "open_trade", return_value=999) as open_t,
        mock.patch.object(engine_mod, "log_trade", return_value=1),
        mock.patch.object(engine_mod, "log_signal"),
        mock.patch.dict(sys.modules, {"MetaTrader5": fake_mt5}),
    ):
        gc.return_value.__enter__.return_value = mock.MagicMock()
        eng._handle_open(hosted, "EURCHF", Decision.open(sig), balance=1000.0, equity=1000.0)

    assert open_t.called
    assert open_t.call_args.kwargs["magic"] == 234007  # effective magic attribution
    assert strat.filled and strat.filled[0][2] == 999  # on_fill with ticket


def test_open_releases_trade_lock_during_broker_call():
    """The broker round-trip (open_trade + its retries) must NOT hold _trade_lock.

    open_trade can sleep ~75s retrying the 00:00 rollover halt (D040); holding the
    lock across that window stalls the monitoring thread (D031, audit #8).  Here a
    fake open_trade asserts the lock is acquirable while it runs, then the trade is
    still recorded afterwards (phase 3 ran under a re-acquired lock).
    """
    sig = _signal(action="buy")
    strat = FakeStrategy(name="lull", decision=Decision.open(sig))
    eng = _make_engine([strat], magics={"lull": 234000})
    hosted = eng.strategies[0]

    fake_mt5 = mock.MagicMock()
    fake_mt5.symbol_info.return_value = SimpleNamespace(
        digits=5, volume_step=0.01, volume_min=0.01, volume_max=100.0, trade_tick_value=1.0
    )
    fake_mt5.positions_get.return_value = [SimpleNamespace(price_open=1.0)]

    lock_free_during_call = {"ok": False}

    def fake_open_trade(*args, **kwargs):
        # The engine thread itself holds nothing here: a non-blocking acquire must
        # succeed, proving the lock is released during the broker round-trip.
        acquired = eng._trade_lock.acquire(blocking=False)
        lock_free_during_call["ok"] = acquired
        if acquired:
            eng._trade_lock.release()
        return 999

    with (
        mock.patch.object(engine_mod, "get_connection") as gc,
        mock.patch.object(engine_mod, "check_drawdown", return_value=(True, "")),
        mock.patch.object(eng, "_check_strategy_drawdown", return_value=(True, "", 1000.0, 1000.0)),
        mock.patch.object(engine_mod, "check_strategy_risk", return_value=(True, "")),
        mock.patch.object(engine_mod, "get_open_positions", return_value=[]),
        mock.patch.object(engine_mod, "get_open_trades", return_value=[]),
        mock.patch.object(engine_mod, "calculate_position_size_allocated", return_value=0.05),
        mock.patch.object(engine_mod, "open_trade", side_effect=fake_open_trade),
        mock.patch.object(engine_mod, "log_trade", return_value=1) as log_trade,
        mock.patch.object(engine_mod, "log_signal"),
        mock.patch.dict(sys.modules, {"MetaTrader5": fake_mt5}),
    ):
        gc.return_value.__enter__.return_value = mock.MagicMock()
        eng._handle_open(hosted, "EURCHF", Decision.open(sig), balance=1000.0, equity=1000.0)

    assert lock_free_during_call["ok"]  # lock was free during the broker call
    assert log_trade.called  # phase 3 still recorded the open
    assert strat.filled and strat.filled[0][2] == 999  # on_fill fired with the ticket


def test_open_rejected_by_risk_calls_on_order_rejected():
    sig = _signal(action="buy")
    strat = FakeStrategy(name="lull", decision=Decision.open(sig))
    eng = _make_engine([strat])
    hosted = eng.strategies[0]

    with (
        mock.patch.object(engine_mod, "get_connection") as gc,
        mock.patch.object(engine_mod, "check_drawdown", return_value=(True, "")),
        mock.patch.object(eng, "_check_strategy_drawdown", return_value=(True, "", 1000.0, 1000.0)),
        mock.patch.object(
            engine_mod, "check_strategy_risk", return_value=(False, "max trades reached (4/4)")
        ),
        mock.patch.object(engine_mod, "get_open_positions", return_value=[]),
        mock.patch.object(engine_mod, "get_open_trades", return_value=[]),
        mock.patch.object(engine_mod, "log_signal") as log_sig,
        mock.patch.object(engine_mod, "open_trade") as open_t,
    ):
        gc.return_value.__enter__.return_value = mock.MagicMock()
        eng._handle_open(hosted, "EURCHF", Decision.open(sig), balance=1000.0, equity=1000.0)

    assert not open_t.called  # never executed
    assert strat.rejected  # on_order_rejected fired
    assert log_sig.call_args.kwargs.get("rejection_reason")  # logged as rejected


def test_open_global_drawdown_trips_kill_switch():
    sig = _signal(action="buy")
    strat = FakeStrategy(name="lull", decision=Decision.open(sig))
    eng = _make_engine([strat])
    hosted = eng.strategies[0]

    with (
        mock.patch.object(engine_mod, "get_connection") as gc,
        mock.patch.object(
            engine_mod, "check_drawdown", return_value=(False, "drawdown 12% exceeds limit 10%")
        ),
        mock.patch.object(engine_mod, "log_event"),
        mock.patch.object(engine_mod, "log_signal"),
        mock.patch.object(engine_mod, "open_trade") as open_t,
    ):
        gc.return_value.__enter__.return_value = mock.MagicMock()
        eng._handle_open(hosted, "EURCHF", Decision.open(sig), balance=1000.0, equity=880.0)

    assert eng.state.paused is True  # global kill switch
    assert not open_t.called
    assert strat.rejected


def test_close_all_closes_only_strategy_positions():
    strat = FakeStrategy(name="lull", decision=Decision.close_all("session_close"))
    eng = _make_engine([strat], magics={"lull": 234000})
    hosted = eng.strategies[0]

    positions = [
        {
            "ticket": 1,
            "pair": "EURCHF",
            "direction": "buy",
            "volume": 0.05,
            "price_open": 1.0,
            "profit": 2.0,
        },
    ]
    fake_mt5 = mock.MagicMock()
    fake_mt5.history_deals_get.return_value = [SimpleNamespace(price=1.01, profit=2.5)]

    with (
        mock.patch.object(engine_mod, "get_open_positions", return_value=positions) as gop,
        mock.patch.object(engine_mod, "close_trade", return_value=True) as ct,
        mock.patch.object(engine_mod, "get_balance", return_value=1002.5),
        mock.patch.object(engine_mod, "get_connection") as gc,
        mock.patch.object(
            engine_mod,
            "get_trade_by_ticket",
            return_value={"id": 1, "closed_at": None, "duration_minutes": 30},
        ),
        mock.patch.object(engine_mod, "close_trade_record"),
        mock.patch.object(engine_mod, "log_signal"),
        mock.patch.dict(sys.modules, {"MetaTrader5": fake_mt5}),
    ):
        gc.return_value.__enter__.return_value = mock.MagicMock()
        eng._handle_close_all(hosted, Decision.close_all("session_close"))

    # get_open_positions queried with the strategy's magic.
    assert gop.call_args.args[0] == 234000
    # close_trade called with the strategy's magic.
    assert ct.call_args.kwargs["magic"] == 234000


def test_close_all_isolates_per_position_failure():
    strat = FakeStrategy(name="lull", decision=Decision.close_all("session_close"))
    eng = _make_engine([strat])
    hosted = eng.strategies[0]

    positions = [
        {
            "ticket": 1,
            "pair": "EURCHF",
            "direction": "buy",
            "volume": 0.05,
            "price_open": 1.0,
            "profit": 2.0,
        },
        {
            "ticket": 2,
            "pair": "EURJPY",
            "direction": "sell",
            "volume": 0.05,
            "price_open": 150.0,
            "profit": -1.0,
        },
    ]

    def close_side_effect(**kwargs):
        if kwargs["ticket"] == 1:
            raise RuntimeError("boom")
        return True

    fake_mt5 = mock.MagicMock()
    fake_mt5.history_deals_get.return_value = [SimpleNamespace(price=150.0, profit=-1.0)]

    with (
        mock.patch.object(engine_mod, "get_open_positions", return_value=positions),
        mock.patch.object(engine_mod, "close_trade", side_effect=close_side_effect) as ct,
        mock.patch.object(engine_mod, "get_balance", return_value=1000.0),
        mock.patch.object(engine_mod, "get_connection") as gc,
        mock.patch.object(
            engine_mod,
            "get_trade_by_ticket",
            return_value={"id": 2, "closed_at": None, "duration_minutes": 30},
        ),
        mock.patch.object(engine_mod, "close_trade_record"),
        mock.patch.dict(sys.modules, {"MetaTrader5": fake_mt5}),
    ):
        gc.return_value.__enter__.return_value = mock.MagicMock()
        # Must not raise despite ticket 1 failing.
        eng._handle_close_all(hosted, Decision.close_all("session_close"))

    # Both positions were attempted (failure isolated).
    assert ct.call_count == 2


def test_close_all_notifies_once_for_n_pairs_in_one_tick():
    """N pairs returning close_all in one tick must produce ONE notification.

    Regression for the duplicate "BOT STOPPED" alerts: the Daily Lull returns
    close_all from every pair at the 02:00 stop, and _dispatch_strategy must dedup
    it to a single close + a single calm session_closed notification (D057).
    """
    pairs = ["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"]
    strat = FakeStrategy(name="lull", pairs=pairs, decision=Decision.close_all("session_close"))
    eng = _make_engine([strat])
    hosted = eng.strategies[0]

    notifies: list[tuple] = []
    eng._notify_bot_status = lambda bot, chat, status, detail="": notifies.append((status, detail))

    boundary = datetime(2026, 6, 2, 2, 0, tzinfo=_SERVER_TZ)
    market = mock.MagicMock()

    handled: list[Decision] = []
    with (
        mock.patch.object(eng, "_handle_close_all", side_effect=lambda h, d: handled.append(d)),
        mock.patch.object(engine_mod, "get_open_positions", return_value=[]),
    ):
        eng._dispatch_strategy(hosted, boundary, market, balance=1000.0, equity=1000.0)

    # on_bar ran for every pair (transparency), but close_all was handled once.
    assert len(strat.on_bar_calls) == len(pairs)
    assert len(handled) == 1


def test_close_all_session_close_uses_calm_status():
    """The session-close notification is 'session_closed', never the alarming 'stopped'.

    The notification fires only when at least one position was actually closed (#7).
    Quiet nights (zero open positions) must not generate noise.
    """
    strat = FakeStrategy(name="lull", decision=Decision.close_all("session_close"))
    eng = _make_engine([strat])
    hosted = eng.strategies[0]

    notifies: list[tuple] = []
    eng._notify_bot_status = lambda bot, chat, status, detail="": notifies.append((status, detail))

    fake_pos = {"ticket": 1, "pair": "EURCHF"}

    with (
        mock.patch.object(engine_mod, "get_open_positions", return_value=[fake_pos]),
        mock.patch.object(engine_mod, "get_connection") as gc,
        mock.patch.object(engine_mod, "log_signal"),
        mock.patch.object(eng, "_close_one"),  # skip real close machinery
    ):
        gc.return_value.__enter__.return_value = mock.MagicMock()
        eng._handle_close_all(hosted, Decision.close_all("session_close"))

    assert len(notifies) == 1
    status, _detail = notifies[0]
    assert status == "session_closed"  # not "stopped"


def test_close_all_session_close_heartbeat_when_no_positions():
    """session_close with zero open positions still fires a calm heartbeat (D066).

    The earlier rule stayed silent on quiet nights (#7); that hid days of
    non-trading in production, so the 02:00 close now always pings for liveness.
    """
    strat = FakeStrategy(name="lull", decision=Decision.close_all("session_close"))
    eng = _make_engine([strat])
    hosted = eng.strategies[0]

    notifies: list[tuple] = []
    eng._notify_bot_status = lambda bot, chat, status, detail="": notifies.append((status, detail))

    with (
        mock.patch.object(engine_mod, "get_open_positions", return_value=[]),
        mock.patch.object(engine_mod, "get_connection") as gc,
        mock.patch.object(engine_mod, "log_signal"),
    ):
        gc.return_value.__enter__.return_value = mock.MagicMock()
        eng._handle_close_all(hosted, Decision.close_all("session_close"))

    assert len(notifies) == 1
    status, detail = notifies[0]
    assert status == "session_closed"
    assert "no trades" in detail


def test_session_start_notification_on_first_wake_after_gap():
    """The first dispatch after a multi-hour gap sends one calm session_started note."""
    strat = FakeStrategy(name="lull", pairs=["EURCHF"], decision=Decision.noop())
    eng = _make_engine([strat])
    hosted = eng.strategies[0]

    notifies: list[tuple] = []
    eng._notify_bot_status = lambda bot, chat, status, detail="": notifies.append((status, detail))

    market = mock.MagicMock()
    with mock.patch.object(engine_mod, "get_open_positions", return_value=[]):
        # First wake of the process at 21:00 (no previous boundary) → session start.
        eng._dispatch_strategy(
            hosted, datetime(2026, 6, 2, 21, 0, tzinfo=_SERVER_TZ), market, 1000.0, 1000.0
        )
        # Next M15 close 15 min later → no new session-start notification.
        eng._dispatch_strategy(
            hosted, datetime(2026, 6, 2, 21, 15, tzinfo=_SERVER_TZ), market, 1000.0, 1000.0
        )

    starts = [n for n in notifies if n[0] == "session_started"]
    assert len(starts) == 1


# ---------------------------------------------------------------------------
# Pause semantics (D064): open blocked, close/close_all always execute
# ---------------------------------------------------------------------------


def test_paused_strategy_close_all_executes(monkeypatch):
    """A paused strategy dispatches on_bar and its close_all fires (D064)."""
    close_all_decision = Decision.close_all("session_close")
    strat = FakeStrategy(name="lull", pairs=["EURCHF"], decision=close_all_decision)
    eng = _make_engine([strat])
    hosted = eng.strategies[0]
    hosted.paused = True  # strategy-level pause

    closed: list = []
    eng._handle_close_all = lambda h, d: closed.append(d)
    eng._notify_bot_status = lambda *a, **kw: None

    market = mock.MagicMock()
    with mock.patch.object(engine_mod, "get_open_positions", return_value=[]):
        eng._dispatch_strategy(
            hosted, datetime(2026, 6, 2, 2, 0, tzinfo=_SERVER_TZ), market, 1000.0, 1000.0
        )

    assert len(strat.on_bar_calls) == 1  # on_bar ran
    assert len(closed) == 1  # close_all executed


def test_open_dropped_under_strategy_pause():
    """An open decision is dropped (not opened) when the strategy is paused (D064)."""
    sig = _signal(action="buy")
    strat = FakeStrategy(name="lull", decision=Decision.open(sig))
    eng = _make_engine([strat])
    hosted = eng.strategies[0]
    hosted.paused = True  # strategy-level pause

    with (
        mock.patch.object(engine_mod, "get_connection") as gc,
        mock.patch.object(engine_mod, "log_signal") as log_sig,
        mock.patch.object(engine_mod, "open_trade") as open_t,
    ):
        gc.return_value.__enter__.return_value = mock.MagicMock()
        eng._handle_decision(hosted, "EURCHF", Decision.open(sig), 1000.0, 1000.0)

    assert not open_t.called  # no trade opened
    assert strat.rejected  # on_order_rejected fired
    assert strat.rejected[0][2] == "paused"  # reason is "paused"
    assert log_sig.call_args.kwargs.get("rejection_reason") == "paused"  # logged as rejected


def test_open_dropped_under_global_pause():
    """An open decision is dropped when the bot is globally paused (D064)."""
    sig = _signal(action="buy")
    strat = FakeStrategy(name="lull", decision=Decision.open(sig))
    eng = _make_engine([strat])
    hosted = eng.strategies[0]
    eng.state.paused = True  # global kill switch

    with (
        mock.patch.object(engine_mod, "get_connection") as gc,
        mock.patch.object(engine_mod, "log_signal") as log_sig,
        mock.patch.object(engine_mod, "open_trade") as open_t,
    ):
        gc.return_value.__enter__.return_value = mock.MagicMock()
        eng._handle_decision(hosted, "EURCHF", Decision.open(sig), 1000.0, 1000.0)

    assert not open_t.called
    assert strat.rejected
    assert strat.rejected[0][2] == "paused"
    assert log_sig.call_args.kwargs.get("rejection_reason") == "paused"


def test_close_all_executes_under_global_pause():
    """A close_all decision executes even when the bot is globally paused (D064)."""
    strat = FakeStrategy(name="lull", decision=Decision.close_all("session_close"))
    eng = _make_engine([strat])
    hosted = eng.strategies[0]
    eng.state.paused = True

    closed: list = []
    eng._handle_close_all = lambda h, d: closed.append(d)

    eng._handle_decision(hosted, "EURCHF", Decision.close_all("session_close"), 1000.0, 1000.0)

    assert len(closed) == 1


def test_pause_log_fires_once_per_tick_not_per_pair():
    """Pause log message fires at most once per dispatch, not once per pair (D064 #9).

    The old code logged inside the per-pair loop (once per pair); the new code
    logs a single guard before the loop.  We verify this structurally: with 3
    pairs all returning noop, on_bar is called 3 times (per-pair), but the
    pause guard code path executes once (we check this by temporarily restoring
    logging and capturing records).
    """
    import logging as _logging

    strat = FakeStrategy(
        name="lull",
        pairs=["EURCHF", "EURJPY", "GBPJPY"],
        decision=Decision.noop(),
    )
    eng = _make_engine([strat])
    hosted = eng.strategies[0]
    hosted.paused = True

    eng._notify_bot_status = lambda *a, **kw: None

    log_records: list[str] = []

    class _Capture(_logging.Handler):
        def emit(self, record):
            log_records.append(record.getMessage())

    handler = _Capture()
    handler.setLevel(_logging.DEBUG)

    # Override logging.disable so we can capture records regardless of
    # what test_backtest_engine.py did (logging.disable is a global).
    saved_disable = _logging.root.manager.disable
    _logging.disable(_logging.NOTSET)
    engine_mod.logger.addHandler(handler)
    original_level = engine_mod.logger.level
    engine_mod.logger.setLevel(_logging.DEBUG)
    try:
        market = mock.MagicMock()
        with mock.patch.object(engine_mod, "get_open_positions", return_value=[]):
            eng._dispatch_strategy(
                hosted, datetime(2026, 6, 2, 23, 0, tzinfo=_SERVER_TZ), market, 1000.0, 1000.0
            )
    finally:
        engine_mod.logger.removeHandler(handler)
        engine_mod.logger.setLevel(original_level)
        _logging.disable(saved_disable)

    # on_bar was called once per pair (3 times)
    assert len(strat.on_bar_calls) == 3
    # but the pause-notice log fired exactly once (before the loop, not inside)
    pause_logs = [r for r in log_records if "open decisions" in r]
    assert len(pause_logs) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
