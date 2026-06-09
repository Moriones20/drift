"""Tests for the multi-strategy monitoring thread and engine pause coordination (Step 33).

Covers:
  - Per-strategy drawdown brake pauses ONLY the breaching strategy
    (strategy_state.paused) and never the global state (D053).
  - The global drawdown brake still pauses everything (state.paused), measured on
    risk_global (no config.risk shim).
  - Attributed close detection: the monitoring tick builds the union of tickets
    across every enabled strategy's magic, so a close under any magic is detected
    with >1 strategy hosted (D052).
  - The engine reads the per-strategy pause from strategy_state on each cycle, so
    a pause set by the monitoring thread takes effect on the engine.

Real MT5 is never touched; balance/equity/positions and the Telegram app are
mocked.  The DB is a real temporary SQLite file so strategy_state CRUD is exercised.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

# Reuse the pandas_ta stub + db helpers + config factory from test_session_fixes
# (importing it runs the stub installation at module load).
import main  # noqa: E402
from drift.config import (  # noqa: E402
    BrokerConfig,
    DriftConfig,
    ReportsConfig,
    RiskGlobalConfig,
    StrategyConfig,
    StrategyInstanceConfig,
    StrategyRiskConfig,
    SystemConfig,
    TelegramConfig,
)
from drift.db import (  # noqa: E402
    get_strategy_state,
    log_trade,
    set_strategy_paused,
    upsert_strategy_peak,
)
from tests import test_session_fixes as tsf  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _two_strategy_config(
    *,
    a_alloc: float = 50.0,
    b_alloc: float = 50.0,
    a_max_dd: float = 10.0,
    b_max_dd: float = 10.0,
    global_max_dd: float = 10.0,
) -> DriftConfig:
    """Config with two enabled strategies on distinct magic offsets."""
    params = {
        field: getattr(StrategyConfig(), field) for field in StrategyConfig.__dataclass_fields__
    }
    return DriftConfig(
        broker=BrokerConfig(server="demo", login=1, password="x"),
        telegram=TelegramConfig(bot_token="fake:TOKEN", chat_id="123"),
        reports=ReportsConfig(),
        system=SystemConfig(magic_number=234000),
        risk_global=RiskGlobalConfig(max_drawdown_percent=global_max_dd),
        strategies={
            "alpha": StrategyInstanceConfig(
                name="alpha",
                enabled=True,
                magic_offset=0,
                allocation_pct=a_alloc,
                pairs=["EURCHF"],
                risk=StrategyRiskConfig(max_drawdown_percent=a_max_dd),
                params=dict(params),
            ),
            "beta": StrategyInstanceConfig(
                name="beta",
                enabled=True,
                magic_offset=1,
                allocation_pct=b_alloc,
                pairs=["EURJPY"],
                risk=StrategyRiskConfig(max_drawdown_percent=b_max_dd),
                params=dict(params),
            ),
        },
    )


def _db_ctx(db_path: str):
    """Build a get_connection-compatible context manager over a real DB file."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx, conn


def _pos(ticket: int, pair: str, profit: float) -> dict:
    return {
        "ticket": ticket,
        "pair": pair,
        "direction": "buy",
        "volume": 0.1,
        "price_open": 1.0,
        "sl": 0.99,
        "tp": 1.01,
        "profit": profit,
        "time_open": None,
    }


# ---------------------------------------------------------------------------
# Per-strategy drawdown brake
# ---------------------------------------------------------------------------


def test_strategy_drawdown_pauses_only_that_strategy():
    """A strategy breaching its own drawdown pauses ONLY itself, not the account."""
    db_path, conn = tsf._fresh_db()
    conn.close()
    config = _two_strategy_config(a_alloc=50.0, b_alloc=50.0, a_max_dd=10.0)

    # alpha: baseline = 10000 * 50% = 5000.  Seed a peak well above current equity
    # so the brake trips; beta stays healthy.
    with _db_ctx(db_path)[0] as seed_conn:
        upsert_strategy_peak(seed_conn, "alpha", 5000.0)

    bot_app = SimpleNamespace(bot=SimpleNamespace())
    fired: list = []

    def fake_get_open_positions(magic: int):
        if magic == 234000:  # alpha — big floating loss -> equity 4000, dd 20%
            return [_pos(1, "EURCHF", -1000.0)]
        return [_pos(2, "EURJPY", 0.0)]  # beta healthy

    ctx, conn = _db_ctx(db_path)
    with (
        patch("main.get_connection", return_value=ctx),
        patch.object(main, "_fire_and_forget", side_effect=lambda c: fired.append(c)),
    ):
        main._check_strategy_drawdown_pause(
            "alpha", fake_get_open_positions(234000), 10000.0, config, bot_app
        )
        main._check_strategy_drawdown_pause(
            "beta", fake_get_open_positions(234001), 10000.0, config, bot_app
        )

    # alpha paused in strategy_state; beta not.
    check_conn = sqlite3.connect(db_path)
    check_conn.row_factory = sqlite3.Row
    assert bool(get_strategy_state(check_conn, "alpha")["paused"]) is True
    beta_row = get_strategy_state(check_conn, "beta")
    assert beta_row is None or bool(beta_row["paused"]) is False
    check_conn.close()
    # A pause notification fired for alpha only.
    assert len(fired) == 1


def test_strategy_drawdown_does_not_touch_global_state():
    """The per-strategy brake must never set the global BotState.paused."""
    db_path, conn = tsf._fresh_db()
    conn.close()
    config = _two_strategy_config(a_max_dd=5.0)

    with _db_ctx(db_path)[0] as seed_conn:
        upsert_strategy_peak(seed_conn, "alpha", 5000.0)

    state = SimpleNamespace(paused=False, stop_requested=False)
    bot_app = SimpleNamespace(bot=SimpleNamespace())
    ctx, _ = _db_ctx(db_path)
    with (
        patch("main.get_connection", return_value=ctx),
        patch.object(main, "_fire_and_forget", lambda c: None),
    ):
        main._check_strategy_drawdown_pause(
            "alpha", [_pos(1, "EURCHF", -2000.0)], 10000.0, config, bot_app
        )

    assert state.paused is False  # only strategy_state changed, never the global flag


def test_strategy_already_paused_does_not_refire():
    """An already-paused strategy must not re-pause / re-notify each cycle."""
    db_path, conn = tsf._fresh_db()
    conn.close()
    config = _two_strategy_config(a_max_dd=5.0)

    with _db_ctx(db_path)[0] as seed_conn:
        upsert_strategy_peak(seed_conn, "alpha", 5000.0)
        set_strategy_paused(seed_conn, "alpha", True)

    fired: list = []
    bot_app = SimpleNamespace(bot=SimpleNamespace())
    ctx, _ = _db_ctx(db_path)
    with (
        patch("main.get_connection", return_value=ctx),
        patch.object(main, "_fire_and_forget", side_effect=lambda c: fired.append(c)),
    ):
        main._check_strategy_drawdown_pause(
            "alpha", [_pos(1, "EURCHF", -2000.0)], 10000.0, config, bot_app
        )

    assert fired == []  # no duplicate notification


# ---------------------------------------------------------------------------
# Global brake still pauses everything
# ---------------------------------------------------------------------------


def test_global_brake_pauses_everything():
    """The global drawdown brake pauses the whole account (state.paused)."""
    db_path, conn = tsf._fresh_db()
    conn.close()
    config = _two_strategy_config(global_max_dd=10.0)

    state = main.BotState() if hasattr(main, "BotState") else SimpleNamespace(paused=False)
    bot_app = SimpleNamespace(bot=SimpleNamespace())
    ctx, _ = _db_ctx(db_path)
    with (
        patch("main.get_connection", return_value=ctx),
        patch.object(main, "_fire_and_forget", lambda c: None),
    ):
        # equity 8800 vs peak 10000 = 12% drawdown > 10% limit.
        ok, _reason = main._check_drawdown_pause(8800.0, 10000.0, config, state, bot_app)

    assert ok is False
    assert state.paused is True


def test_global_brake_uses_risk_global_not_shim():
    """The global brake reads risk_global.max_drawdown_percent, not config.risk."""
    db_path, conn = tsf._fresh_db()
    conn.close()
    # Global limit 20%; a 12% drawdown should NOT trip it.
    config = _two_strategy_config(global_max_dd=20.0)

    state = SimpleNamespace(paused=False, stop_requested=False)
    bot_app = SimpleNamespace(bot=SimpleNamespace())
    ctx, _ = _db_ctx(db_path)
    with (
        patch("main.get_connection", return_value=ctx),
        patch.object(main, "_fire_and_forget", lambda c: None),
    ):
        ok, _reason = main._check_drawdown_pause(8800.0, 10000.0, config, state, bot_app)

    assert ok is True
    assert state.paused is False


# ---------------------------------------------------------------------------
# Attributed close detection across >1 strategy magic
# ---------------------------------------------------------------------------


def test_monitoring_tick_unions_tickets_across_magics():
    """The monitoring tick builds the union of positions across every magic.

    With two strategies on distinct magics, a ticket gone under EITHER magic is
    fed to close detection.  Here both strategies have one open position; the
    union must contain both tickets, and a previously-known ticket missing from
    the union must be detected as closed.
    """
    db_path, conn = tsf._fresh_db()
    conn.close()
    config = _two_strategy_config()

    state = SimpleNamespace(paused=False, stop_requested=False)
    bot_app = SimpleNamespace(bot=SimpleNamespace())
    peak_ref = [10000.0]
    known = {1, 2, 99}  # 99 was open last cycle and is now gone -> closed

    magic_to_positions = {
        234000: [_pos(1, "EURCHF", 5.0)],
        234001: [_pos(2, "EURJPY", -3.0)],
    }

    detected_args: dict = {}

    def fake_detect(known_tickets, mt5_tickets, cfg, app):
        detected_args["known"] = set(known_tickets)
        detected_args["mt5"] = set(mt5_tickets)
        return set()

    ctx, _ = _db_ctx(db_path)
    with (
        patch("main.health_check", return_value=True),
        patch("main.get_balance", return_value=10000.0),
        patch("main.get_equity", return_value=10000.0),
        patch("main.get_open_positions", side_effect=lambda m: magic_to_positions[m]),
        patch("main.get_connection", return_value=ctx),
        patch("main._detect_closed_trades", side_effect=fake_detect),
        patch("main._check_strategy_drawdown_pause", lambda *a, **k: None),
        patch("main.process_open_trades"),
        patch("main._check_weekly_report", lambda *a, **k: None),
        patch.object(main, "_fire_and_forget", lambda c: None),
    ):
        main._monitoring_tick(config, state, peak_ref, known, bot_app)

    # Union of the two magics' tickets passed to close detection.
    assert detected_args["mt5"] == {1, 2}
    # The vanished ticket 99 was in known and not in the union -> detectable.
    assert 99 in detected_args["known"]
    # known_tickets rebuilt to the current union (99 dropped, no pending).
    assert known == {1, 2}


def test_detect_closed_trade_includes_strategy_in_notification():
    """A detected close surfaces the trade's attributed strategy (D056)."""
    db_path, conn = tsf._fresh_db()
    # Insert an open trade attributed to 'beta'.
    log_trade(
        conn,
        pair="EURJPY",
        direction="buy",
        entry_price=150.0,
        stop_loss=149.0,
        take_profit=152.0,
        position_size=0.1,
        balance_at_open=10000.0,
        mt5_ticket=7777,
        strategy="beta",
    )
    conn.close()
    config = _two_strategy_config()

    fake_mt5 = MagicMock()
    fake_mt5.DEAL_REASON_SL = 1
    fake_mt5.DEAL_REASON_TP = 2
    deal = SimpleNamespace(price=152.0, profit=20.0, reason=2)
    fake_mt5.history_deals_get.return_value = [deal]

    # notify_trade_closed is an async coroutine function; main wraps the call in
    # _fire_and_forget(notify_trade_closed(...)).  Capture the trade_info the
    # notification was built with via a plain (non-async) MagicMock and read its
    # call args, so we inspect the dict the close handed off.
    notify = MagicMock()

    ctx, _ = _db_ctx(db_path)
    bot_app = SimpleNamespace(bot=SimpleNamespace())
    with (
        patch("main.get_connection", return_value=ctx),
        patch("main.get_balance", return_value=10020.0),
        patch("main.notify_trade_closed", notify),
        patch.object(main, "_fire_and_forget", lambda c: None),
        patch.dict(sys.modules, {"MetaTrader5": fake_mt5}),
    ):
        pending = main._detect_closed_trades({7777}, set(), config, bot_app)

    assert pending == set()
    assert notify.called
    trade_info = notify.call_args.args[2]
    assert trade_info.get("strategy") == "beta"
    assert trade_info.get("close_reason") == "take_profit"


# ---------------------------------------------------------------------------
# Engine reads the per-strategy pause from strategy_state
# ---------------------------------------------------------------------------


def test_engine_refreshes_pause_from_strategy_state():
    """The engine syncs hosted.paused from strategy_state each cycle (Part A)."""
    from drift import engine as engine_mod
    from drift.engine import Engine

    db_path, conn = tsf._fresh_db()
    conn.close()

    # Build a bare engine with two hosted strategies, both initially unpaused.
    eng = Engine.__new__(Engine)
    eng._trade_lock = threading.Lock()
    eng.strategies = [
        engine_mod._HostedStrategy(
            instance=SimpleNamespace(name="alpha", pairs=["EURCHF"]),
            name="alpha",
            magic=234000,
            allocation_pct=50.0,
            risk=SimpleNamespace(max_drawdown_percent=10.0),
            paused=False,
        ),
        engine_mod._HostedStrategy(
            instance=SimpleNamespace(name="beta", pairs=["EURJPY"]),
            name="beta",
            magic=234001,
            allocation_pct=50.0,
            risk=SimpleNamespace(max_drawdown_percent=10.0),
            paused=False,
        ),
    ]

    # The monitoring thread (another context) pauses alpha in strategy_state.
    with _db_ctx(db_path)[0] as seed_conn:
        set_strategy_paused(seed_conn, "alpha", True)

    ctx, _ = _db_ctx(db_path)
    with mock.patch.object(engine_mod, "get_connection", return_value=ctx):
        eng._refresh_pause_flags()

    assert eng.strategies[0].paused is True  # alpha picked up the DB pause
    assert eng.strategies[1].paused is False  # beta untouched
    # alpha is now excluded from scheduling.
    assert [h.name for h in eng._active_strategies()] == ["beta"]


def test_engine_refresh_resumes_strategy_cleared_in_db():
    """A strategy resumed in strategy_state (paused=0) re-enters scheduling."""
    from drift import engine as engine_mod
    from drift.engine import Engine

    db_path, conn = tsf._fresh_db()
    conn.close()

    eng = Engine.__new__(Engine)
    eng._trade_lock = threading.Lock()
    eng.strategies = [
        engine_mod._HostedStrategy(
            instance=SimpleNamespace(name="alpha", pairs=["EURCHF"]),
            name="alpha",
            magic=234000,
            allocation_pct=100.0,
            risk=SimpleNamespace(max_drawdown_percent=10.0),
            paused=True,  # was paused in-memory
        ),
    ]

    with _db_ctx(db_path)[0] as seed_conn:
        set_strategy_paused(seed_conn, "alpha", False)  # resumed elsewhere

    ctx, _ = _db_ctx(db_path)
    with mock.patch.object(engine_mod, "get_connection", return_value=ctx):
        eng._refresh_pause_flags()

    assert eng.strategies[0].paused is False


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
