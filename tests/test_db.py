"""Tests for drift/db.py — per-strategy attribution (D056) and strategy_state CRUD."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import drift.db as db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> Path:
    """Initialise a fresh DB in tmp_path and return its path."""
    p = tmp_path / "test.db"
    db.init_db(p)
    return p


def _make_signal() -> MagicMock:
    """Return a minimal mock Signal for log_signal calls."""
    now = datetime(2026, 1, 1, 22, 0, 0, tzinfo=timezone.utc)
    sig = MagicMock()
    sig.pair = "EURCHF"
    sig.action = "buy"
    sig.reason = "range_low_touch"
    sig.timestamp = now
    sig.m15_candle_time = now
    sig.h4_candle_time = now
    sig.range_high = 0.9200
    sig.range_low = 0.9150
    sig.range_atr_ratio = 2.1
    sig.rsi = 32.0
    sig.atr_value = 0.00025
    sig.h4_adx = 18.0
    return sig


# ---------------------------------------------------------------------------
# Schema: strategy column present after init
# ---------------------------------------------------------------------------


class TestSchemaStrategyColumns:
    def test_trades_has_strategy_column(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        conn = sqlite3.connect(p)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
        conn.close()
        assert "strategy" in cols

    def test_signals_has_strategy_column(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        conn = sqlite3.connect(p)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
        conn.close()
        assert "strategy" in cols

    def test_bot_events_has_strategy_column(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        conn = sqlite3.connect(p)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(bot_events)").fetchall()}
        conn.close()
        assert "strategy" in cols

    def test_strategy_state_table_exists(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        conn = sqlite3.connect(p)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_state'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_idx_trades_strategy_exists(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        conn = sqlite3.connect(p)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_trades_strategy'"
        ).fetchone()
        conn.close()
        assert row is not None


# ---------------------------------------------------------------------------
# Migration idempotence: old DB without strategy columns
# ---------------------------------------------------------------------------


class TestMigrationIdempotence:
    def _make_old_db(self, tmp_path: Path) -> Path:
        """Create a DB with the pre-D056 schema (no strategy columns anywhere)."""
        p = tmp_path / "old.db"
        conn = sqlite3.connect(p)
        conn.executescript(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('buy', 'sell')),
                entry_price REAL NOT NULL,
                exit_price REAL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                position_size REAL NOT NULL,
                profit_loss REAL,
                balance_at_open REAL NOT NULL,
                balance_at_close REAL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                close_reason TEXT CHECK(close_reason IN (
                    'trailing_stop', 'take_profit', 'stop_loss',
                    'manual', 'drawdown_pause', 'session_close'
                )),
                duration_minutes INTEGER,
                mt5_ticket INTEGER
            );
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                m15_candle_time TEXT NOT NULL,
                h4_candle_time TEXT NOT NULL,
                range_high REAL,
                range_low REAL,
                range_atr_ratio REAL,
                rsi REAL,
                atr_value REAL,
                h4_adx REAL,
                decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
                reason TEXT NOT NULL,
                trade_id INTEGER REFERENCES trades(id)
            );
            CREATE TABLE bot_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_at TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN (
                    'start', 'stop', 'pause', 'resume', 'error', 'reconnect',
                    'drawdown_alert', 'peak_balance'
                )),
                detail TEXT,
                balance REAL
            );
            INSERT INTO trades (pair, direction, entry_price, stop_loss, take_profit,
                                position_size, balance_at_open, opened_at)
            VALUES ('EURCHF', 'buy', 0.915, 0.910, 0.920, 0.01, 1000.0,
                    '2026-01-01T22:00:00+00:00');
            INSERT INTO signals (pair, analyzed_at, m15_candle_time, h4_candle_time,
                                 decision, reason)
            VALUES ('EURCHF', '2026-01-01T22:00:00+00:00', '2026-01-01T21:45:00+00:00',
                    '2026-01-01T20:00:00+00:00', 'accepted', 'range_low_touch');
            """
        )
        conn.commit()
        conn.close()
        return p

    def test_migration_adds_strategy_column_to_trades(self, tmp_path: Path) -> None:
        p = self._make_old_db(tmp_path)
        db.init_db(p)
        conn = sqlite3.connect(p)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
        conn.close()
        assert "strategy" in cols

    def test_migration_adds_strategy_column_to_signals(self, tmp_path: Path) -> None:
        p = self._make_old_db(tmp_path)
        db.init_db(p)
        conn = sqlite3.connect(p)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
        conn.close()
        assert "strategy" in cols

    def test_migration_adds_strategy_column_to_bot_events(self, tmp_path: Path) -> None:
        p = self._make_old_db(tmp_path)
        db.init_db(p)
        conn = sqlite3.connect(p)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(bot_events)").fetchall()}
        conn.close()
        assert "strategy" in cols

    def test_historical_rows_get_default_strategy(self, tmp_path: Path) -> None:
        p = self._make_old_db(tmp_path)
        db.init_db(p)
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        trade = conn.execute("SELECT strategy FROM trades LIMIT 1").fetchone()
        signal = conn.execute("SELECT strategy FROM signals LIMIT 1").fetchone()
        conn.close()
        assert trade["strategy"] == "daily_lull"
        assert signal["strategy"] == "daily_lull"

    def test_migration_creates_strategy_state_table(self, tmp_path: Path) -> None:
        p = self._make_old_db(tmp_path)
        db.init_db(p)
        conn = sqlite3.connect(p)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_state'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_migration_creates_idx_trades_strategy(self, tmp_path: Path) -> None:
        p = self._make_old_db(tmp_path)
        db.init_db(p)
        conn = sqlite3.connect(p)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_trades_strategy'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Running init_db twice on an already-migrated DB must not raise."""
        p = self._make_old_db(tmp_path)
        db.init_db(p)
        db.init_db(p)  # second call — must not fail or duplicate columns


# ---------------------------------------------------------------------------
# log_trade: strategy persisted, default 'daily_lull'
# ---------------------------------------------------------------------------


class TestLogTradeStrategy:
    def test_default_strategy_is_daily_lull(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            tid = db.log_trade(
                conn,
                pair="EURCHF",
                direction="buy",
                entry_price=0.915,
                stop_loss=0.910,
                take_profit=0.920,
                position_size=0.01,
                balance_at_open=1000.0,
            )
            row = conn.execute("SELECT strategy FROM trades WHERE id = ?", (tid,)).fetchone()
        assert row["strategy"] == "daily_lull"

    def test_explicit_strategy_persisted(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            tid = db.log_trade(
                conn,
                pair="GBPJPY",
                direction="sell",
                entry_price=195.0,
                stop_loss=195.5,
                take_profit=194.5,
                position_size=0.01,
                balance_at_open=1000.0,
                strategy="london_breakout",
            )
            row = conn.execute("SELECT strategy FROM trades WHERE id = ?", (tid,)).fetchone()
        assert row["strategy"] == "london_breakout"


# ---------------------------------------------------------------------------
# log_signal: strategy persisted, default 'daily_lull'
# ---------------------------------------------------------------------------


class TestLogSignalStrategy:
    def test_default_strategy_is_daily_lull(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        sig = _make_signal()
        with db.get_connection(p) as conn:
            sid = db.log_signal(conn, sig)
            row = conn.execute("SELECT strategy FROM signals WHERE id = ?", (sid,)).fetchone()
        assert row["strategy"] == "daily_lull"

    def test_explicit_strategy_persisted(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        sig = _make_signal()
        with db.get_connection(p) as conn:
            sid = db.log_signal(conn, sig, strategy="london_breakout")
            row = conn.execute("SELECT strategy FROM signals WHERE id = ?", (sid,)).fetchone()
        assert row["strategy"] == "london_breakout"


# ---------------------------------------------------------------------------
# log_event: strategy persisted, default None (global)
# ---------------------------------------------------------------------------


class TestLogEventStrategy:
    def test_default_strategy_is_none(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            eid = db.log_event(conn, "start", detail="bot started")
            row = conn.execute("SELECT strategy FROM bot_events WHERE id = ?", (eid,)).fetchone()
        assert row["strategy"] is None

    def test_explicit_strategy_persisted(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            eid = db.log_event(conn, "pause", strategy="daily_lull", detail="drawdown hit")
            row = conn.execute("SELECT strategy FROM bot_events WHERE id = ?", (eid,)).fetchone()
        assert row["strategy"] == "daily_lull"


# ---------------------------------------------------------------------------
# log_peak_balance: strategy param accepted
# ---------------------------------------------------------------------------


class TestLogPeakBalance:
    def test_global_peak_no_strategy(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.log_peak_balance(conn, 1050.0)
            row = conn.execute(
                "SELECT strategy FROM bot_events WHERE event_type = 'peak_balance'"
            ).fetchone()
        assert row["strategy"] is None

    def test_per_strategy_peak(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.log_peak_balance(conn, 1050.0, strategy="daily_lull")
            row = conn.execute(
                "SELECT strategy FROM bot_events WHERE event_type = 'peak_balance'"
            ).fetchone()
        assert row["strategy"] == "daily_lull"


# ---------------------------------------------------------------------------
# strategy_state CRUD
# ---------------------------------------------------------------------------


class TestStrategyState:
    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            result = db.get_strategy_state(conn, "daily_lull")
        assert result is None

    def test_upsert_peak_creates_row(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)
            state = db.get_strategy_state(conn, "daily_lull")
        assert state is not None
        assert state["strategy"] == "daily_lull"
        assert state["peak_equity"] == pytest.approx(1000.0)
        assert state["paused"] == 0

    def test_upsert_peak_updates_to_higher_value(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)
            db.upsert_strategy_peak(conn, "daily_lull", 1100.0)
            state = db.get_strategy_state(conn, "daily_lull")
        assert state["peak_equity"] == pytest.approx(1100.0)

    def test_upsert_peak_does_not_decrease(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.upsert_strategy_peak(conn, "daily_lull", 1100.0)
            db.upsert_strategy_peak(conn, "daily_lull", 900.0)
            state = db.get_strategy_state(conn, "daily_lull")
        assert state["peak_equity"] == pytest.approx(1100.0)

    def test_set_paused_creates_row_if_absent(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.set_strategy_paused(conn, "daily_lull", True)
            state = db.get_strategy_state(conn, "daily_lull")
        assert state is not None
        assert state["paused"] == 1

    def test_set_paused_false(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.set_strategy_paused(conn, "daily_lull", True)
            db.set_strategy_paused(conn, "daily_lull", False)
            state = db.get_strategy_state(conn, "daily_lull")
        assert state["paused"] == 0

    def test_set_paused_does_not_reset_peak(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.upsert_strategy_peak(conn, "daily_lull", 1200.0)
            db.set_strategy_paused(conn, "daily_lull", True)
            state = db.get_strategy_state(conn, "daily_lull")
        assert state["peak_equity"] == pytest.approx(1200.0)

    def test_seed_baseline_overwrites_peak_directly(self, tmp_path: Path) -> None:
        # seed_strategy_baseline is a DIRECT write (not MAX): it can LOWER the
        # stored peak, unlike upsert_strategy_peak (D062).
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.upsert_strategy_peak(conn, "daily_lull", 5000.0)
            db.seed_strategy_baseline(conn, "daily_lull", 800.0, 1000.0)
            state = db.get_strategy_state(conn, "daily_lull")
        assert state["peak_equity"] == pytest.approx(1000.0)  # lowered from 5000
        assert state["baseline_capital"] == pytest.approx(800.0)

    def test_get_all_strategy_states_empty(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            result = db.get_all_strategy_states(conn)
        assert result == []

    def test_get_all_strategy_states_multiple(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)
            db.upsert_strategy_peak(conn, "london_breakout", 500.0)
            result = db.get_all_strategy_states(conn)
        names = [r["strategy"] for r in result]
        assert "daily_lull" in names
        assert "london_breakout" in names
        assert len(result) == 2

    def test_updated_at_is_utc_iso8601(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)
            state = db.get_strategy_state(conn, "daily_lull")
        # Must parse without error and be timezone-aware
        dt = datetime.fromisoformat(state["updated_at"])
        assert dt.tzinfo is not None


# ---------------------------------------------------------------------------
# get_stats: strategy filter
# ---------------------------------------------------------------------------


class TestGetStatsStrategyFilter:
    def _insert_closed_trade(
        self, conn: sqlite3.Connection, strategy: str, profit_loss: float
    ) -> None:
        conn.execute(
            """
            INSERT INTO trades (strategy, pair, direction, entry_price, stop_loss, take_profit,
                                position_size, balance_at_open, opened_at, closed_at,
                                exit_price, profit_loss, close_reason, duration_minutes)
            VALUES (?, 'EURCHF', 'buy', 0.915, 0.910, 0.920, 0.01, 1000.0,
                    '2026-01-01T22:00:00+00:00', '2026-01-01T22:30:00+00:00',
                    0.919, ?, 'take_profit', 30)
            """,
            (strategy, profit_loss),
        )
        conn.commit()

    def test_no_filter_aggregates_all(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            self._insert_closed_trade(conn, "daily_lull", 10.0)
            self._insert_closed_trade(conn, "london_breakout", 20.0)
            stats = db.get_stats(conn)
        assert stats["total_trades"] == 2
        assert stats["total_pnl"] == pytest.approx(30.0)

    def test_filter_by_strategy(self, tmp_path: Path) -> None:
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            self._insert_closed_trade(conn, "daily_lull", 10.0)
            self._insert_closed_trade(conn, "london_breakout", 20.0)
            stats = db.get_stats(conn, strategy="daily_lull")
        assert stats["total_trades"] == 1
        assert stats["total_pnl"] == pytest.approx(10.0)

    def test_peak_balance_events_filtered_by_strategy(self, tmp_path: Path) -> None:
        # peak_balance events for two strategies; the higher one belongs to the
        # OTHER strategy.  get_stats(strategy=) must NOT leak it (D062 #5).
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.log_peak_balance(conn, 1100.0, strategy="daily_lull")
            db.log_peak_balance(conn, 5000.0, strategy="london_breakout")
            daily = db.get_stats(conn, strategy="daily_lull")
            london = db.get_stats(conn, strategy="london_breakout")
            all_stats = db.get_stats(conn)
        assert daily["peak_balance"] == pytest.approx(1100.0)  # not 5000
        assert london["peak_balance"] == pytest.approx(5000.0)
        assert all_stats["peak_balance"] == pytest.approx(5000.0)  # None aggregates


# ---------------------------------------------------------------------------
# Canonical per-strategy equity / drawdown (D062)
# ---------------------------------------------------------------------------


class TestEvaluateStrategyDrawdown:
    def _insert_closed_trade(
        self, conn: sqlite3.Connection, strategy: str, profit_loss: float
    ) -> None:
        conn.execute(
            """
            INSERT INTO trades (strategy, pair, direction, entry_price, stop_loss, take_profit,
                                position_size, balance_at_open, opened_at, closed_at,
                                exit_price, profit_loss, close_reason, duration_minutes)
            VALUES (?, 'EURCHF', 'buy', 0.915, 0.910, 0.920, 0.01, 1000.0,
                    '2026-01-01T22:00:00+00:00', '2026-01-01T22:30:00+00:00',
                    0.919, ?, 'take_profit', 30)
            """,
            (strategy, profit_loss),
        )
        conn.commit()

    def test_seed_removes_double_count_at_100pct(self, tmp_path: Path) -> None:
        # Balance 1200 includes +200 of past realized; allocation 100%.  On the
        # first evaluation the baseline is seeded and equity must equal the
        # allocated slice (1200), NOT slice + realized (1400) — the old bug.
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            self._insert_closed_trade(conn, "daily_lull", 200.0)
            ok, _reason, equity, peak = db.evaluate_strategy_drawdown(
                conn, "daily_lull", 100.0, 1200.0, 0.0, 10.0
            )
            state = db.get_strategy_state(conn, "daily_lull")
        assert equity == pytest.approx(1200.0)
        assert peak == pytest.approx(1200.0)
        assert ok is True
        assert state["baseline_capital"] == pytest.approx(1000.0)  # 1200 - 200

    def test_forward_realized_counted_once(self, tmp_path: Path) -> None:
        # Seed with 200 realized, then 50 more realized forward; equity advances
        # by exactly +50 from the seed slice (1200 -> 1250), counted once.
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            self._insert_closed_trade(conn, "daily_lull", 200.0)
            db.evaluate_strategy_drawdown(conn, "daily_lull", 100.0, 1200.0, 0.0, 10.0)
            self._insert_closed_trade(conn, "daily_lull", 50.0)  # forward realized
            _ok, _reason, equity, _peak = db.evaluate_strategy_drawdown(
                conn, "daily_lull", 100.0, 1250.0, 0.0, 10.0
            )
        assert equity == pytest.approx(1250.0)

    def test_inflated_peak_is_reset_on_first_seed(self, tmp_path: Path) -> None:
        # A pre-existing inflated peak (e.g. from the old double-counting code)
        # must be reset to the freshly computed equity on the first seed (D062).
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.upsert_strategy_peak(conn, "daily_lull", 9999.0)  # inflated, baseline NULL
            ok, _reason, equity, peak = db.evaluate_strategy_drawdown(
                conn, "daily_lull", 100.0, 1000.0, 0.0, 10.0
            )
            state = db.get_strategy_state(conn, "daily_lull")
        assert equity == pytest.approx(1000.0)
        assert peak == pytest.approx(1000.0)  # NOT 9999
        assert state["peak_equity"] == pytest.approx(1000.0)
        assert ok is True  # drawdown restarts clean from the deploy

    def test_seeded_baseline_persists_and_brake_trips(self, tmp_path: Path) -> None:
        # After seeding, a later floating loss trips the brake against the
        # established baseline/peak (baseline no longer reseeded).
        p = _make_db(tmp_path)
        with db.get_connection(p) as conn:
            db.evaluate_strategy_drawdown(conn, "daily_lull", 100.0, 1000.0, 0.0, 10.0)
            ok, reason, equity, _peak = db.evaluate_strategy_drawdown(
                conn, "daily_lull", 100.0, 1000.0, -150.0, 10.0
            )
        assert equity == pytest.approx(850.0)  # 1000 baseline + 0 realized - 150 floating
        assert ok is False
        assert "strategy drawdown" in reason
