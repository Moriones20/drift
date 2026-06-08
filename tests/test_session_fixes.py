"""Regression tests for the session-cycle fixes (D030-D034).

Covers:
- DB accepts session_close / rejects nothing valid (D030)
- trades table migration from old friday_close CHECK (D030)
- _close_session_trades isolates per-position failures (D031)
- _detect_closed_trades labels unknown deal reason as 'manual' (D034)
- _weekly_trigger_utc converts local day+hour to UTC correctly (D032)
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path.
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# pandas_ta stub — reuse the same pattern from test_e2e.py so imports work
# on Python environments where pandas_ta is broken (e.g. Python 3.14 + tqdm).
# ---------------------------------------------------------------------------


def _try_import_pandas_ta() -> bool:
    try:
        import pandas_ta  # noqa: F401

        return True
    except Exception:
        return False


if not _try_import_pandas_ta() and "pandas_ta" not in sys.modules:
    import pandas as pd

    _pta_stub = MagicMock()

    def _rsi_stub(series, length=14, **_kw):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1.0 / length, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / length, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        result = 100.0 - (100.0 / (1.0 + rs))
        result.name = f"RSI_{length}"
        return result

    def _adx_stub(high, low, close, length=14, **_kw):
        prev_high = high.shift(1)
        prev_low = low.shift(1)
        prev_close = close.shift(1)
        plus_dm = (high - prev_high).clip(lower=0)
        minus_dm = (prev_low - low).clip(lower=0)
        mask = plus_dm >= minus_dm
        plus_dm = plus_dm.where(mask, 0.0)
        minus_dm = minus_dm.where(~mask, 0.0)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        alpha = 1.0 / length
        atr_val = tr.ewm(alpha=alpha, adjust=False).mean()
        _nan = float("nan")
        plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_val.replace(0, _nan)
        minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_val.replace(0, _nan)
        di_sum = (plus_di + minus_di).replace(0, float("nan"))
        dx = 100.0 * (plus_di - minus_di).abs() / di_sum
        adx_val = dx.ewm(alpha=alpha, adjust=False).mean()
        return pd.DataFrame(
            {
                f"ADX_{length}": adx_val,
                f"DMP_{length}": plus_di,
                f"DMN_{length}": minus_di,
            }
        )

    def _atr_stub(high, low, close, length=14, **_kw):
        tr = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(span=length, adjust=False).mean()
        atr.name = f"ATRr_{length}"
        return atr

    _pta_stub.rsi = _rsi_stub
    _pta_stub.adx = _adx_stub
    _pta_stub.atr = _atr_stub
    sys.modules["pandas_ta"] = _pta_stub

# Now safe to import drift modules.
from drift.config import ReportsConfig  # noqa: E402
from drift.db import (  # noqa: E402
    close_trade_record,
    get_open_trades,
    init_db,
    log_trade,
)

# ---------------------------------------------------------------------------
# Shared config factory (used by TestCloseSessionTradesIsolation and
# TestDetectClosedTradesManualLabel — kept here to avoid duplication).
# ---------------------------------------------------------------------------


def _make_minimal_config():
    from drift.config import (
        BrokerConfig,
        DriftConfig,
        ReportsConfig,
        RiskConfig,
        StrategyConfig,
        SystemConfig,
        TelegramConfig,
    )

    return DriftConfig(
        broker=BrokerConfig(server="demo", login=1, password="x"),
        strategy=StrategyConfig(),
        risk=RiskConfig(),
        telegram=TelegramConfig(bot_token="fake:TOKEN", chat_id="123"),
        reports=ReportsConfig(),
        system=SystemConfig(),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The old schema used single-quoted string literals in CHECK constraints (standard SQL).
# We build it via string concatenation to avoid quote-escaping inside a triple-quoted string.
_OLD_TRADES_SCHEMA = (
    "CREATE TABLE trades ("
    "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "    pair TEXT NOT NULL,"
    "    direction TEXT NOT NULL CHECK(direction IN ('buy', 'sell')),"
    "    entry_price REAL NOT NULL,"
    "    exit_price REAL,"
    "    stop_loss REAL NOT NULL,"
    "    take_profit REAL NOT NULL,"
    "    position_size REAL NOT NULL,"
    "    profit_loss REAL,"
    "    balance_at_open REAL NOT NULL,"
    "    balance_at_close REAL,"
    "    opened_at TEXT NOT NULL,"
    "    closed_at TEXT,"
    "    close_reason TEXT CHECK(close_reason IN ("
    "        'trailing_stop', 'take_profit', 'stop_loss',"
    "        'manual', 'drawdown_pause', 'friday_close'"
    "    )),"
    "    duration_minutes INTEGER,"
    "    mt5_ticket INTEGER"
    ");"
)


def _make_temp_db_path() -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return tmp.name


def _fresh_db() -> tuple[str, sqlite3.Connection]:
    """Create a temp DB, run init_db, return (path, connection)."""
    db_path = _make_temp_db_path()
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return db_path, conn


def _insert_open_trade(conn: sqlite3.Connection, ticket: int = 1001) -> int:
    return log_trade(
        conn,
        pair="EURCHF",
        direction="buy",
        entry_price=0.9500,
        stop_loss=0.9400,
        take_profit=0.9700,
        position_size=0.10,
        balance_at_open=10000.0,
        mt5_ticket=ticket,
    )


# ---------------------------------------------------------------------------
# Test 1: DB accepts session_close
# ---------------------------------------------------------------------------


class TestDbAcceptsSessionClose(unittest.TestCase):
    """D030: close_reason='session_close' must insert without error."""

    def setUp(self) -> None:
        self.db_path, self.conn = _fresh_db()

    def tearDown(self) -> None:
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def test_session_close_inserts_without_error(self) -> None:
        trade_id = _insert_open_trade(self.conn)
        # Must not raise.
        close_trade_record(
            self.conn,
            trade_id=trade_id,
            exit_price=0.9600,
            profit_loss=50.0,
            balance_at_close=10050.0,
            close_reason="session_close",
        )
        row = self.conn.execute(
            "SELECT close_reason FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
        self.assertEqual(row["close_reason"], "session_close")

    def test_session_close_row_round_trips(self) -> None:
        trade_id = _insert_open_trade(self.conn, ticket=2001)
        close_trade_record(
            self.conn,
            trade_id=trade_id,
            exit_price=0.9550,
            profit_loss=25.0,
            balance_at_close=10025.0,
            close_reason="session_close",
        )
        open_after = get_open_trades(self.conn)
        self.assertEqual(len(open_after), 0)

        row = self.conn.execute(
            "SELECT exit_price, profit_loss, close_reason FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        self.assertAlmostEqual(row["exit_price"], 0.9550)
        self.assertAlmostEqual(row["profit_loss"], 25.0)
        self.assertEqual(row["close_reason"], "session_close")

    def test_all_valid_close_reasons_accepted(self) -> None:
        """Smoke-test every valid close_reason value so no regression is silently added."""
        valid_reasons = [
            "trailing_stop",
            "take_profit",
            "stop_loss",
            "manual",
            "drawdown_pause",
            "session_close",
        ]
        for i, reason in enumerate(valid_reasons):
            with self.subTest(reason=reason):
                trade_id = _insert_open_trade(self.conn, ticket=3000 + i)
                close_trade_record(
                    self.conn,
                    trade_id=trade_id,
                    exit_price=0.9600,
                    profit_loss=0.0,
                    balance_at_close=10000.0,
                    close_reason=reason,
                )
                row = self.conn.execute(
                    "SELECT close_reason FROM trades WHERE id = ?", (trade_id,)
                ).fetchone()
                self.assertEqual(row["close_reason"], reason)


# ---------------------------------------------------------------------------
# Test 2: trades table migration
# ---------------------------------------------------------------------------


class TestTradesMigration(unittest.TestCase):
    """D030: init_db migrates old friday_close CHECK to session_close, idempotently.

    Rows carrying close_reason='friday_close' are remapped to 'session_close' during
    the INSERT…SELECT so the migration succeeds regardless of existing data.
    """

    def _build_old_schema_db(
        self,
        rows: list[dict] | None = None,
    ) -> str:
        """Create a DB with the OLD trades CHECK (friday_close, no session_close).

        rows: list of dicts with columns to insert; None means insert two neutral rows.
        """
        db_path = _make_temp_db_path()
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(_OLD_TRADES_SCHEMA)
            if rows is None:
                # Two rows that are safe to migrate — neither uses friday_close.
                conn.execute(
                    """
                    INSERT INTO trades (
                        pair, direction, entry_price, stop_loss, take_profit,
                        position_size, balance_at_open, opened_at
                    ) VALUES ('AUDNZD', 'sell', 1.0800, 1.0900, 1.0600, 0.10, 5000.0,
                              '2024-01-15T22:00:00+00:00')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO trades (
                        pair, direction, entry_price, stop_loss, take_profit,
                        position_size, balance_at_open, opened_at,
                        exit_price, profit_loss, balance_at_close, closed_at,
                        close_reason, duration_minutes
                    ) VALUES ('EURCHF', 'buy', 0.9500, 0.9400, 0.9700, 0.10, 5000.0,
                              '2024-01-15T21:00:00+00:00',
                              0.9620, 60.0, 5060.0, '2024-01-16T02:00:00+00:00',
                              'take_profit', 300)
                    """
                )
            elif rows:
                for r in rows:
                    cols = ", ".join(r.keys())
                    placeholders = ", ".join("?" for _ in r)
                    conn.execute(
                        f"INSERT INTO trades ({cols}) VALUES ({placeholders})",
                        list(r.values()),
                    )
            conn.commit()
        finally:
            conn.close()
        return db_path

    def tearDown(self) -> None:
        # Each test is responsible for its own cleanup via try/finally.
        pass

    def test_migration_preserves_all_rows(self) -> None:
        db_path = self._build_old_schema_db()  # two rows with safe close_reasons
        try:
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()
                self.assertEqual(rows["n"], 2)
            finally:
                conn.close()
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_migration_allows_session_close_insert(self) -> None:
        db_path = self._build_old_schema_db()
        try:
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                trade_id = _insert_open_trade(conn, ticket=9001)
                # Must not raise IntegrityError after migration.
                close_trade_record(
                    conn,
                    trade_id=trade_id,
                    exit_price=0.9600,
                    profit_loss=10.0,
                    balance_at_close=5010.0,
                    close_reason="session_close",
                )
                row = conn.execute(
                    "SELECT close_reason FROM trades WHERE id = ?", (trade_id,)
                ).fetchone()
                self.assertEqual(row["close_reason"], "session_close")
            finally:
                conn.close()
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_migration_is_idempotent(self) -> None:
        """Running init_db twice on an already-migrated DB must not error or corrupt data."""
        db_path = self._build_old_schema_db()
        try:
            init_db(db_path)
            init_db(db_path)  # Second call — must be a no-op.
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()
                self.assertEqual(rows["n"], 2)
                # 'trades' table must still exist and be the only trades-named table.
                table_names = [
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'trades%'"
                    ).fetchall()
                ]
                self.assertIn("trades", table_names)
                # No leftover trades_new from a re-run.
                self.assertNotIn("trades_new", table_names)
            finally:
                conn.close()
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_migration_empty_table_succeeds(self) -> None:
        """Migration with zero rows must still complete without error."""
        db_path = self._build_old_schema_db(rows=[])
        try:
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()
                self.assertEqual(rows["n"], 0)
            finally:
                conn.close()
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_migration_remaps_friday_close_to_session_close(self) -> None:
        """Migration must succeed when a row carries close_reason='friday_close' and
        remap that value to 'session_close' (its modern equivalent). All other fields
        must be preserved verbatim and no rows may be lost.
        """
        db_path = self._build_old_schema_db(
            rows=[
                {
                    "pair": "EURCHF",
                    "direction": "buy",
                    "entry_price": 0.95,
                    "stop_loss": 0.94,
                    "take_profit": 0.97,
                    "position_size": 0.10,
                    "balance_at_open": 5000.0,
                    "opened_at": "2024-01-15T21:00:00+00:00",
                    "exit_price": 0.962,
                    "profit_loss": 60.0,
                    "balance_at_close": 5060.0,
                    "closed_at": "2024-01-16T02:00:00+00:00",
                    "close_reason": "friday_close",
                    "duration_minutes": 300,
                }
            ]
        )
        try:
            # Must not raise — the CASE expression remaps the value before INSERT.
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT * FROM trades").fetchall()
                self.assertEqual(len(rows), 1, "No rows must be lost during migration")
                row = rows[0]
                self.assertEqual(row["close_reason"], "session_close")
                # Verify all other fields are preserved.
                self.assertEqual(row["pair"], "EURCHF")
                self.assertEqual(row["direction"], "buy")
                self.assertAlmostEqual(row["entry_price"], 0.95)
                self.assertAlmostEqual(row["exit_price"], 0.962)
                self.assertAlmostEqual(row["profit_loss"], 60.0)
                self.assertAlmostEqual(row["balance_at_close"], 5060.0)
                self.assertEqual(row["duration_minutes"], 300)
            finally:
                conn.close()
        finally:
            Path(db_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 3: _close_session_trades isolates per-position failures
# ---------------------------------------------------------------------------


class TestCloseSessionTradesIsolation(unittest.TestCase):
    """D031: a failure on one position must not abort the rest, and must not raise."""

    def _make_position(self, ticket: int, pair: str) -> dict:
        return {
            "ticket": ticket,
            "pair": pair,
            "direction": "buy",
            "volume": 0.10,
            "price_open": 0.9500,
            "profit": 20.0,
        }

    def setUp(self) -> None:
        self.db_path, self.conn = _fresh_db()
        self.config = _make_minimal_config()

        # Pre-insert DB records so _close_session_trades can find them by ticket.
        self.ticket_a = 7001
        self.ticket_b = 7002
        self.ticket_c = 7003
        _insert_open_trade(self.conn, ticket=self.ticket_a)
        _insert_open_trade(self.conn, ticket=self.ticket_b)
        _insert_open_trade(self.conn, ticket=self.ticket_c)
        self.conn.close()  # close so init_db-produced path is used via get_connection

    def tearDown(self) -> None:
        Path(self.db_path).unlink(missing_ok=True)

    def test_remaining_positions_closed_when_first_raises(self) -> None:
        """Position A raises on close_trade; positions B and C must still be processed."""
        from drift.strategy import SessionState
        from main import _close_session_trades

        positions = [
            self._make_position(self.ticket_a, "EURCHF"),
            self._make_position(self.ticket_b, "AUDNZD"),
            self._make_position(self.ticket_c, "EURGBP"),
        ]

        close_trade_calls: list[int] = []

        def _fake_close_trade(ticket, pair, lot_size, direction, magic):
            if ticket == self.ticket_a:
                raise RuntimeError("Simulated MT5 close failure")
            close_trade_calls.append(ticket)
            return True

        bot_app = MagicMock()
        bot_app.bot = MagicMock()

        session_states: dict = {
            "EURCHF": SessionState(),
            "AUDNZD": SessionState(),
            "EURGBP": SessionState(),
        }

        with (
            patch("main.get_open_positions", return_value=positions),
            patch("main.close_trade", side_effect=_fake_close_trade),
            patch("main.get_balance", return_value=10000.0),
            patch("main.get_connection") as mock_get_conn,
        ):
            # Provide a real temp DB connection through the context manager mock.
            real_conn = sqlite3.connect(self.db_path)
            real_conn.row_factory = sqlite3.Row
            real_conn.execute("PRAGMA foreign_keys = ON")
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=real_conn)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_get_conn.return_value = ctx

            # Must not raise even though position A fails.
            try:
                _close_session_trades(
                    config=self.config,
                    session_states=session_states,
                    bot_app=bot_app,
                )
            finally:
                real_conn.close()

        # B and C were passed to close_trade (A raised before reaching its append).
        self.assertIn(self.ticket_b, close_trade_calls)
        self.assertIn(self.ticket_c, close_trade_calls)

    def test_function_does_not_raise_on_total_failure(self) -> None:
        """All positions fail — the function must still return normally."""
        from drift.strategy import SessionState
        from main import _close_session_trades

        positions = [self._make_position(self.ticket_a, "EURCHF")]

        bot_app = MagicMock()
        bot_app.bot = MagicMock()

        session_states = {"EURCHF": SessionState()}

        with (
            patch("main.get_open_positions", return_value=positions),
            patch("main.close_trade", side_effect=RuntimeError("total failure")),
        ):
            # Must not propagate the exception.
            _close_session_trades(
                config=self.config,
                session_states=session_states,
                bot_app=bot_app,
            )

    def test_balance_at_close_is_settled_balance(self) -> None:
        """D048: persists the fresh settled balance from get_balance(), not a snapshot."""
        from drift.strategy import SessionState
        from main import _close_session_trades

        settled = 9876.54  # distinct from balance_at_open (10000.0)
        positions = [self._make_position(self.ticket_b, "AUDNZD")]
        bot_app = MagicMock()
        bot_app.bot = MagicMock()
        session_states = {"AUDNZD": SessionState()}

        with (
            patch("main.get_open_positions", return_value=positions),
            patch("main.close_trade", return_value=True),
            patch("main.get_balance", return_value=settled),
            patch("main.get_connection") as mock_get_conn,
        ):
            real_conn = sqlite3.connect(self.db_path)
            real_conn.row_factory = sqlite3.Row
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=real_conn)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_get_conn.return_value = ctx
            try:
                _close_session_trades(
                    config=self.config,
                    session_states=session_states,
                    bot_app=bot_app,
                )
                row = real_conn.execute(
                    "SELECT balance_at_close FROM trades WHERE mt5_ticket = ?",
                    (self.ticket_b,),
                ).fetchone()
            finally:
                real_conn.close()

        self.assertAlmostEqual(row["balance_at_close"], settled)


# ---------------------------------------------------------------------------
# Test 4: _detect_closed_trades labels unknown reason as 'manual'
# ---------------------------------------------------------------------------


class TestDetectClosedTradesManualLabel(unittest.TestCase):
    """D034: a deal whose MT5 reason is neither SL nor TP is recorded as 'manual'."""

    def setUp(self) -> None:
        self.db_path, self.conn = _fresh_db()
        self.config = _make_minimal_config()
        self.ticket = 8001
        _insert_open_trade(self.conn, ticket=self.ticket)
        self.conn.close()

    def tearDown(self) -> None:
        Path(self.db_path).unlink(missing_ok=True)

    def _run_detect_on_db(self, db_path: str, ticket: int, mt5_deal_reason: int) -> str:
        """Run _detect_closed_trades with a fake deal and return the recorded close_reason.

        Operates on the given db_path/ticket pair so it can be reused both by the
        setUp-initialised self.db_path and by ad-hoc databases in subTest loops.
        """
        from main import _detect_closed_trades

        fake_mt5 = MagicMock()
        fake_mt5.DEAL_REASON_SL = 1
        fake_mt5.DEAL_REASON_TP = 2

        fake_deal = MagicMock()
        fake_deal.price = 0.9600
        fake_deal.profit = 15.0
        fake_deal.reason = mt5_deal_reason
        fake_mt5.history_deals_get.return_value = [fake_deal]

        bot_app = MagicMock()
        bot_app.bot = MagicMock()

        real_conn = sqlite3.connect(db_path)
        real_conn.row_factory = sqlite3.Row
        real_conn.execute("PRAGMA foreign_keys = ON")

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=real_conn)
        ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch("main.get_connection", return_value=ctx),
            patch("main.get_balance", return_value=10000.0),
            patch.dict("sys.modules", {"MetaTrader5": fake_mt5}),
        ):
            _detect_closed_trades({ticket}, set(), self.config, bot_app)

        row = real_conn.execute(
            "SELECT close_reason FROM trades WHERE mt5_ticket = ?", (ticket,)
        ).fetchone()
        real_conn.close()
        return row["close_reason"] if row else ""

    def _run_detect(self, mt5_deal_reason: int) -> str:
        """Convenience wrapper that uses the setUp DB and ticket."""
        return self._run_detect_on_db(self.db_path, self.ticket, mt5_deal_reason)

    def test_deal_reason_sl_records_stop_loss(self) -> None:
        reason = self._run_detect(mt5_deal_reason=1)  # DEAL_REASON_SL = 1
        self.assertEqual(reason, "stop_loss")

    def test_deal_reason_tp_records_take_profit(self) -> None:
        # Re-insert because the ticket was consumed in the previous test's DB.
        # (Each test has its own setUp, so this is fine.)
        reason = self._run_detect(mt5_deal_reason=2)  # DEAL_REASON_TP = 2
        self.assertEqual(reason, "take_profit")

    def test_deal_reason_client_records_manual(self) -> None:
        """DEAL_REASON_CLIENT (0) is 'closed manually in terminal' — must be 'manual'."""
        reason = self._run_detect(mt5_deal_reason=0)
        self.assertEqual(reason, "manual")

    def test_unknown_reason_never_records_trailing_stop(self) -> None:
        """Any non-SL/TP deal reason must NOT produce 'trailing_stop' (D034)."""
        for deal_reason in (0, 3, 7, 99):
            with self.subTest(deal_reason=deal_reason):
                # Each subTest needs a fresh DB because the call closes the trade.
                db_path, conn = _fresh_db()
                ticket = 8100 + deal_reason
                _insert_open_trade(conn, ticket=ticket)
                conn.close()
                try:
                    reason = self._run_detect_on_db(db_path, ticket, deal_reason)
                finally:
                    Path(db_path).unlink(missing_ok=True)

                self.assertNotEqual(reason, "trailing_stop")
                self.assertEqual(reason, "manual")


# ---------------------------------------------------------------------------
# Test 5: _weekly_trigger_utc
# ---------------------------------------------------------------------------


class TestWeeklyTriggerUtc(unittest.TestCase):
    """D032: _weekly_trigger_utc converts local day+hour to the correct UTC (weekday, hour)."""

    def _trigger(self, day: str, hour: int, tz: str) -> tuple[int, int]:
        from main import _weekly_trigger_utc

        cfg = ReportsConfig(
            weekly_report_day=day,
            weekly_report_hour=hour,
            timezone=tz,
        )
        return _weekly_trigger_utc(cfg)

    def test_default_config_sunday_20_utc_minus5_gives_monday_01(self) -> None:
        """Sunday 20:00 UTC-5 → add 5h → Monday 01:00 UTC → (0, 1)."""
        weekday, hour = self._trigger("sunday", 20, "UTC-5")
        self.assertEqual(weekday, 0)  # Monday
        self.assertEqual(hour, 1)

    def test_utc_plus3_no_rollover(self) -> None:
        """Wednesday 18:00 UTC+3 → subtract 3h → Wednesday 15:00 UTC → (2, 15)."""
        weekday, hour = self._trigger("wednesday", 18, "UTC+3")
        self.assertEqual(weekday, 2)  # Wednesday
        self.assertEqual(hour, 15)

    def test_same_day_no_rollover(self) -> None:
        """Friday 12:00 UTC → (4, 12) — timezone is UTC (offset 0)."""
        weekday, hour = self._trigger("friday", 12, "UTC")
        self.assertEqual(weekday, 4)  # Friday
        self.assertEqual(hour, 12)

    def test_utc_minus8_rolls_day_forward(self) -> None:
        """Saturday 22:00 UTC-8 → add 8h → Sunday 06:00 UTC → (6, 6)."""
        weekday, hour = self._trigger("saturday", 22, "UTC-8")
        self.assertEqual(weekday, 6)  # Sunday
        self.assertEqual(hour, 6)

    def test_utc_plus12_rolls_day_back(self) -> None:
        """Monday 02:00 UTC+12 → subtract 12h → Sunday 14:00 UTC → (6, 14)."""
        weekday, hour = self._trigger("monday", 2, "UTC+12")
        self.assertEqual(weekday, 6)  # Sunday (day wraps backward)
        self.assertEqual(hour, 14)

    def test_case_insensitive_day_name(self) -> None:
        """Day names must be case-insensitive."""
        r1 = self._trigger("Sunday", 20, "UTC-5")
        r2 = self._trigger("SUNDAY", 20, "UTC-5")
        r3 = self._trigger("sunday", 20, "UTC-5")
        self.assertEqual(r1, r2)
        self.assertEqual(r2, r3)

    def test_utc_zero_offset(self) -> None:
        """Explicit UTC+0 — no shift applied."""
        weekday, hour = self._trigger("thursday", 9, "UTC+0")
        self.assertEqual(weekday, 3)  # Thursday
        self.assertEqual(hour, 9)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
