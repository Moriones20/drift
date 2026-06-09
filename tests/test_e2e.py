"""End-to-end tests for the Drift forex bot.

Tests the complete signal → trade → logging flow using real module logic
and a mocked MT5 layer. No MT5 installation required.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

# Ensure project root is on the path when run directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Inject a lightweight pandas_ta stub before any drift module imports it.
# drift/indicators.py does `import pandas_ta as ta` at module level, and
# drift/trailing.py imports from indicators.  When pandas_ta itself is broken
# (e.g. missing tqdm on Python 3.14) we stub it so the pure-logic modules
# (trailing, risk, db, report, strategy) can still be imported and tested.
# ---------------------------------------------------------------------------


def _try_import_pandas_ta() -> bool:
    try:
        import pandas_ta  # noqa: F401

        return True
    except Exception:
        return False


HAS_PANDAS_TA = _try_import_pandas_ta()

if not HAS_PANDAS_TA and "pandas_ta" not in sys.modules:
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
        result = pd.DataFrame(
            {
                f"ADX_{length}": adx_val,
                f"DMP_{length}": plus_di,
                f"DMN_{length}": minus_di,
            }
        )
        return result

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

# Now we can safely import drift modules that depend on pandas_ta.
from drift.config import (  # noqa: E402
    BrokerConfig,
    DriftConfig,
    ReportsConfig,
    RiskConfig,
    RiskGlobalConfig,
    StrategyConfig,
    StrategyInstanceConfig,
    StrategyRiskConfig,
    SystemConfig,
    TelegramConfig,
)
from drift.db import (  # noqa: E402
    close_trade_record,
    get_open_trades,
    get_recent_trades,
    get_stats,
    init_db,
    log_signal,
    log_trade,
)
from drift.report import generate_weekly_report  # noqa: E402
from drift.risk import (  # noqa: E402
    calculate_position_size,
    check_all_risk,
    check_correlation,
    check_drawdown,
    check_max_trades,
)
from drift.trailing import check_tp, update_trailing  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> DriftConfig:
    """Build a minimal DriftConfig with daily_lull strategy for test use."""
    return DriftConfig(
        broker=BrokerConfig(server="demo.icmarkets.com", login=12345, password="secret"),
        telegram=TelegramConfig(bot_token="fake:TOKEN", chat_id="123456"),
        reports=ReportsConfig(),
        system=SystemConfig(),
        risk_global=RiskGlobalConfig(),
        strategies={
            "daily_lull": StrategyInstanceConfig(
                name="daily_lull",
                enabled=True,
                magic_offset=0,
                allocation_pct=100.0,
                pairs=["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"],
                risk=StrategyRiskConfig(),
                params={
                    field: getattr(StrategyConfig(), field)
                    for field in StrategyConfig.__dataclass_fields__
                },
            )
        },
    )


def _make_test_db() -> tuple[str, sqlite3.Connection]:
    """Create a temp DB file, init schema, and return (path, connection)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return db_path, conn


def _make_signal(
    pair: str = "AUDCAD",
    action: str = "buy",
    trend: str = "ranging",  # kept for call-site compatibility; unused
    reason: str = "lull_scalper_buy range_low=0.8950 rsi=28.0 adx=15.0",
) -> "Signal":  # noqa: F821
    """Build a minimal Signal for the Daily Lull Scalper schema."""
    from drift.strategies.base import Signal

    now = datetime.now(timezone.utc)
    return Signal(
        pair=pair,
        timestamp=now,
        m15_candle_time=now,
        h4_candle_time=now,
        entry_price=0.8952,
        sl=0.8900,
        tp=0.9000,
        range_high=0.9050,
        range_low=0.8950,
        range_atr_ratio=2.0,
        rsi=28.0,
        atr_value=0.0050,
        h4_adx=15.0,
        action=action,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Test 1: Full signal-to-trade flow (pure logic, no MT5)
# ---------------------------------------------------------------------------


class TestFullSignalToTradeFlow(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _make_config()
        self.db_path, self.conn = _make_test_db()

    def tearDown(self) -> None:
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def test_risk_check_passes_with_clean_state(self) -> None:
        ok, reason = check_all_risk(
            balance=10000.0,
            peak_balance=10000.0,
            open_trades=[],
            new_pair="EURUSD",
            new_direction="buy",
            config=RiskConfig(),
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_position_size_positive(self) -> None:
        lot = calculate_position_size(
            balance=10000.0,
            risk_percent=1.0,
            stop_loss_pips=50.0,
            pip_value=10.0,
        )
        self.assertGreater(lot, 0)

    def test_trade_and_signal_logged_to_db(self) -> None:
        signal = _make_signal(action="buy")

        trade_id = log_trade(
            self.conn,
            pair="EURUSD",
            direction="buy",
            entry_price=1.08000,
            stop_loss=1.07250,
            take_profit=1.09500,
            position_size=0.10,
            balance_at_open=10000.0,
            mt5_ticket=98765,
        )
        signal_id = log_signal(self.conn, signal, trade_id=trade_id)

        self.assertGreater(trade_id, 0)
        self.assertGreater(signal_id, 0)

        row = self.conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        self.assertEqual(row["pair"], "EURUSD")
        self.assertEqual(row["direction"], "buy")
        self.assertAlmostEqual(row["entry_price"], 1.08000)

        sig_row = self.conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        self.assertEqual(sig_row["decision"], "accepted")
        self.assertEqual(sig_row["trade_id"], trade_id)


# ---------------------------------------------------------------------------
# Test 2: Risk rejection — max trades
# ---------------------------------------------------------------------------


class TestRiskRejectionMaxTrades(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _make_config()
        self.db_path, self.conn = _make_test_db()

    def tearDown(self) -> None:
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def _make_4_open_trades(self) -> list[dict]:
        return [
            {"pair": "EURUSD", "direction": "buy"},
            {"pair": "GBPUSD", "direction": "buy"},
            {"pair": "USDJPY", "direction": "sell"},
            {"pair": "AUDUSD", "direction": "buy"},
        ]

    def test_max_trades_check_fails(self) -> None:
        open_trades = self._make_4_open_trades()
        ok, reason = check_max_trades(open_trades, max_trades=4)
        self.assertFalse(ok)
        self.assertIn("max trades reached", reason)

    def test_check_all_risk_fails_on_max_trades(self) -> None:
        open_trades = self._make_4_open_trades()
        ok, reason = check_all_risk(
            balance=10000.0,
            peak_balance=10000.0,
            open_trades=open_trades,
            new_pair="EURGBP",
            new_direction="buy",
            config=RiskConfig(),
        )
        self.assertFalse(ok)
        self.assertIn("max trades reached", reason)

    def test_rejected_signal_stored_with_rejection_reason(self) -> None:
        open_trades = self._make_4_open_trades()
        ok, reason = check_all_risk(
            balance=10000.0,
            peak_balance=10000.0,
            open_trades=open_trades,
            new_pair="EURGBP",
            new_direction="buy",
            config=RiskConfig(),
        )
        self.assertFalse(ok)

        signal = _make_signal(pair="EURCHF", action="buy")
        rejection_reason = f"risk: {reason}"
        sig_id = log_signal(self.conn, signal, trade_id=None, rejection_reason=rejection_reason)

        row = self.conn.execute("SELECT * FROM signals WHERE id = ?", (sig_id,)).fetchone()
        self.assertEqual(row["decision"], "rejected")
        self.assertIn("max trades reached", row["reason"])


# ---------------------------------------------------------------------------
# Test 3: Correlation rejection
# ---------------------------------------------------------------------------


class TestCorrelationRejection(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _make_config()

    def test_correlation_blocks_third_eur_buy(self) -> None:
        open_trades = [
            {"pair": "EURUSD", "direction": "buy"},
            {"pair": "EURGBP", "direction": "buy"},
        ]
        ok, reason = check_correlation(
            open_trades=open_trades,
            new_pair="EURUSD",
            new_direction="buy",
            max_same=2,
        )
        self.assertFalse(ok)
        self.assertIn("EUR", reason)

    def test_correlation_allows_different_currency(self) -> None:
        open_trades = [
            {"pair": "EURUSD", "direction": "buy"},
            {"pair": "EURGBP", "direction": "buy"},
        ]
        ok, reason = check_correlation(
            open_trades=open_trades,
            new_pair="GBPUSD",
            new_direction="buy",
            max_same=2,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_correlation_buy_eur_counts_eurgbp_sell_as_selling_eur(self) -> None:
        # EURGBP sell → selling EUR (base), buying GBP (quote)
        # EURUSD buy → buying EUR
        # These are not correlated on the EUR buy side.
        open_trades = [
            {"pair": "EURGBP", "direction": "sell"},
        ]
        ok, reason = check_correlation(
            open_trades=open_trades,
            new_pair="EURUSD",
            new_direction="buy",
            max_same=2,
        )
        # Only 1 trade selling EUR and 0 buying EUR — should pass.
        self.assertTrue(ok)

    def test_two_eur_buy_trades_blocks_third_via_check_all_risk(self) -> None:
        open_trades = [
            {"pair": "EURUSD", "direction": "buy"},
            {"pair": "EURGBP", "direction": "buy"},
        ]
        ok, reason = check_all_risk(
            balance=10000.0,
            peak_balance=10000.0,
            open_trades=open_trades,
            new_pair="EURCAD",
            new_direction="buy",
            config=RiskConfig(),
        )
        self.assertFalse(ok)
        self.assertIn("EUR", reason)


# ---------------------------------------------------------------------------
# Test 4: Drawdown pause
# ---------------------------------------------------------------------------


class TestDrawdownPause(unittest.TestCase):
    def test_drawdown_triggers_at_10_percent(self) -> None:
        ok, reason = check_drawdown(
            current_balance=450.0,
            peak_balance=500.0,
            max_drawdown_percent=10.0,
        )
        self.assertFalse(ok)
        self.assertIn("10.0%", reason)

    def test_drawdown_does_not_trigger_below_limit(self) -> None:
        ok, reason = check_drawdown(
            current_balance=460.0,
            peak_balance=500.0,
            max_drawdown_percent=10.0,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_drawdown_exactly_at_limit_triggers(self) -> None:
        # 10% of 500 = 50, so balance 450 is exactly 10%.
        ok, reason = check_drawdown(
            current_balance=450.0,
            peak_balance=500.0,
            max_drawdown_percent=10.0,
        )
        self.assertFalse(ok)

    def test_zero_peak_balance_does_not_crash(self) -> None:
        ok, reason = check_drawdown(
            current_balance=0.0,
            peak_balance=0.0,
            max_drawdown_percent=10.0,
        )
        self.assertTrue(ok)


# ---------------------------------------------------------------------------
# Test 5 (deleted): Friday close logic
# The Daily Lull Scalper never opens positions on Friday (skip-Friday rule in
# _in_session_window), so _check_friday_close is a no-op for this strategy.
# Tests for the filtering algorithm are removed. See DECISIONS.md D029 / Step 6.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 6: Weekly report generation
# ---------------------------------------------------------------------------


class TestWeeklyReportGeneration(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path, self.conn = _make_test_db()

    def tearDown(self) -> None:
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def _insert_closed_trade(
        self,
        pair: str,
        direction: str,
        pnl: float,
        duration_minutes: int = 240,
    ) -> int:
        trade_id = log_trade(
            self.conn,
            pair=pair,
            direction=direction,
            entry_price=1.08,
            stop_loss=1.07,
            take_profit=1.10,
            position_size=0.10,
            balance_at_open=10000.0,
        )
        close_trade_record(
            self.conn,
            trade_id=trade_id,
            exit_price=1.09,
            profit_loss=pnl,
            balance_at_close=10000.0 + pnl,
            close_reason="take_profit",
        )
        # Force duration to a known value.
        self.conn.execute(
            "UPDATE trades SET duration_minutes = ? WHERE id = ?",
            (duration_minutes, trade_id),
        )
        self.conn.commit()
        return trade_id

    def test_report_is_non_empty(self) -> None:
        self._insert_closed_trade("EURUSD", "buy", pnl=50.0)
        report = generate_weekly_report(self.conn, current_balance=10050.0, peak_balance=10050.0)
        self.assertIsInstance(report, str)
        self.assertGreater(len(report), 0)

    def test_report_contains_key_sections(self) -> None:
        self._insert_closed_trade("EURUSD", "buy", pnl=80.0)
        self._insert_closed_trade("GBPUSD", "sell", pnl=-30.0)
        report = generate_weekly_report(self.conn, current_balance=10050.0, peak_balance=10080.0)

        self.assertIn("WEEKLY REPORT", report)
        self.assertIn("SUMMARY", report)
        self.assertIn("Win Rate", report)
        self.assertIn("ACCOUNT", report)
        self.assertIn("Balance", report)

    def test_report_shows_correct_trade_count(self) -> None:
        self._insert_closed_trade("EURUSD", "buy", pnl=50.0)
        self._insert_closed_trade("GBPUSD", "buy", pnl=40.0)
        self._insert_closed_trade("USDJPY", "sell", pnl=-20.0)
        report = generate_weekly_report(self.conn, current_balance=10070.0, peak_balance=10070.0)

        # "Trades: 3 (2W / 1L)"
        self.assertIn("3", report)

    def test_empty_week_report_does_not_crash(self) -> None:
        report = generate_weekly_report(self.conn, current_balance=10000.0, peak_balance=10000.0)
        self.assertIn("No trades this week", report)
        self.assertIn("ACCOUNT", report)

    def test_report_contains_pair_breakdown(self) -> None:
        self._insert_closed_trade("EURUSD", "buy", pnl=100.0)
        self._insert_closed_trade("GBPUSD", "sell", pnl=-40.0)
        report = generate_weekly_report(self.conn, current_balance=10060.0, peak_balance=10100.0)
        self.assertIn("EURUSD", report)
        self.assertIn("GBPUSD", report)


# ---------------------------------------------------------------------------
# Test 7: Trailing stop calculation
# ---------------------------------------------------------------------------


class TestTrailingStopCalculation(unittest.TestCase):
    def _buy_position(self, entry: float = 1.08000, sl: float = 1.07500) -> dict:
        return {
            "ticket": 42,
            "pair": "EURUSD",
            "direction": "buy",
            "volume": 0.10,
            "price_open": entry,
            "sl": sl,
            "tp": 1.10000,
            "profit": 100.0,
            "time_open": datetime.now(timezone.utc),
        }

    def _sell_position(self, entry: float = 1.08000, sl: float = 1.08500) -> dict:
        return {
            "ticket": 43,
            "pair": "EURUSD",
            "direction": "sell",
            "volume": 0.10,
            "price_open": entry,
            "sl": sl,
            "tp": 1.06000,
            "profit": 100.0,
            "time_open": datetime.now(timezone.utc),
        }

    def test_buy_sl_moves_up_when_price_rises(self) -> None:
        position = self._buy_position(entry=1.08000, sl=1.07500)
        atr = 0.005
        multiplier = 1.5
        current_price = 1.09000

        new_sl = update_trailing(
            position=position,
            current_price=current_price,
            atr_value=atr,
            atr_multiplier=multiplier,
        )

        expected_sl = current_price - atr * multiplier  # 1.09000 - 0.0075 = 1.0825
        self.assertIsNotNone(new_sl)
        self.assertAlmostEqual(new_sl, expected_sl, places=5)
        self.assertGreater(new_sl, position["sl"])

    def test_buy_sl_does_not_move_when_price_flat(self) -> None:
        position = self._buy_position(entry=1.08000, sl=1.07500)
        # Price only moved slightly — new SL would be below current SL.
        new_sl = update_trailing(
            position=position,
            current_price=1.08050,
            atr_value=0.005,
            atr_multiplier=1.5,
        )
        self.assertIsNone(new_sl)

    def test_sell_sl_moves_down_when_price_falls(self) -> None:
        position = self._sell_position(entry=1.08000, sl=1.08500)
        atr = 0.005
        multiplier = 1.5
        current_price = 1.07000

        new_sl = update_trailing(
            position=position,
            current_price=current_price,
            atr_value=atr,
            atr_multiplier=multiplier,
        )

        expected_sl = current_price + atr * multiplier  # 1.07000 + 0.0075 = 1.0775
        self.assertIsNotNone(new_sl)
        self.assertAlmostEqual(new_sl, expected_sl, places=5)
        self.assertLess(new_sl, position["sl"])

    def test_sell_sl_does_not_move_when_price_rises(self) -> None:
        position = self._sell_position(entry=1.08000, sl=1.08500)
        new_sl = update_trailing(
            position=position,
            current_price=1.08100,
            atr_value=0.005,
            atr_multiplier=1.5,
        )
        self.assertIsNone(new_sl)

    def test_check_tp_buy_triggers_above_tp(self) -> None:
        position = {**self._buy_position(), "tp": 1.09500}
        self.assertTrue(check_tp(position, current_price=1.09600))

    def test_check_tp_buy_does_not_trigger_below_tp(self) -> None:
        position = {**self._buy_position(), "tp": 1.09500}
        self.assertFalse(check_tp(position, current_price=1.09000))

    def test_check_tp_sell_triggers_below_tp(self) -> None:
        position = {**self._sell_position(), "tp": 1.06000}
        self.assertTrue(check_tp(position, current_price=1.05900))

    def test_check_tp_zero_tp_never_triggers(self) -> None:
        position = {**self._buy_position(), "tp": 0.0}
        self.assertFalse(check_tp(position, current_price=99.0))


# ---------------------------------------------------------------------------
# Test 8: Signal logging completeness (shadow trading data)
# ---------------------------------------------------------------------------


class TestSignalLoggingCompleteness(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path, self.conn = _make_test_db()

    def tearDown(self) -> None:
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def test_none_signal_logged_as_rejected(self) -> None:
        signal = _make_signal(action="none", reason="trending_market")
        sig_id = log_signal(self.conn, signal, trade_id=None)

        row = self.conn.execute("SELECT * FROM signals WHERE id = ?", (sig_id,)).fetchone()
        self.assertEqual(row["decision"], "rejected")
        self.assertEqual(row["reason"], "trending_market")

    def test_all_indicator_values_stored(self) -> None:
        """Daily Lull Scalper signal fields (range_high, range_low, h4_adx, etc.) are persisted."""
        signal = _make_signal(action="buy")
        sig_id = log_signal(self.conn, signal, trade_id=None)

        row = self.conn.execute("SELECT * FROM signals WHERE id = ?", (sig_id,)).fetchone()
        self.assertIsNotNone(row["range_high"])
        self.assertIsNotNone(row["range_low"])
        self.assertIsNotNone(row["range_atr_ratio"])
        self.assertIsNotNone(row["rsi"])
        self.assertIsNotNone(row["h4_adx"])
        self.assertIsNotNone(row["atr_value"])
        self.assertIsNotNone(row["m15_candle_time"])
        self.assertIsNotNone(row["h4_candle_time"])

    def test_risk_rejected_signal_stores_risk_reason(self) -> None:
        signal = _make_signal(action="buy", pair="EURUSD")
        rejection = "risk: max trades reached (4/4)"
        sig_id = log_signal(self.conn, signal, trade_id=None, rejection_reason=rejection)

        row = self.conn.execute("SELECT * FROM signals WHERE id = ?", (sig_id,)).fetchone()
        self.assertEqual(row["decision"], "rejected")
        self.assertIn("max trades reached", row["reason"])

    def test_accepted_signal_linked_to_trade(self) -> None:
        trade_id = log_trade(
            self.conn,
            pair="EURUSD",
            direction="buy",
            entry_price=1.08,
            stop_loss=1.075,
            take_profit=1.095,
            position_size=0.10,
            balance_at_open=10000.0,
        )
        signal = _make_signal(action="buy")
        sig_id = log_signal(self.conn, signal, trade_id=trade_id)

        row = self.conn.execute("SELECT * FROM signals WHERE id = ?", (sig_id,)).fetchone()
        self.assertEqual(row["decision"], "accepted")
        self.assertEqual(row["trade_id"], trade_id)

    def test_multiple_pairs_logged_independently(self) -> None:
        for pair in ["AUDCAD", "NZDCAD", "EURCHF"]:
            signal = _make_signal(pair=pair, action="none", reason="trending_market")
            log_signal(self.conn, signal)

        rows = self.conn.execute("SELECT pair FROM signals ORDER BY pair").fetchall()
        pairs = [r["pair"] for r in rows]
        self.assertIn("AUDCAD", pairs)
        self.assertIn("NZDCAD", pairs)
        self.assertIn("EURCHF", pairs)


# ---------------------------------------------------------------------------
# Test 9: Database CRUD correctness
# ---------------------------------------------------------------------------


class TestDatabaseCRUD(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path, self.conn = _make_test_db()

    def tearDown(self) -> None:
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def test_log_and_close_trade(self) -> None:
        trade_id = log_trade(
            self.conn,
            pair="EURUSD",
            direction="buy",
            entry_price=1.08000,
            stop_loss=1.07250,
            take_profit=1.09500,
            position_size=0.10,
            balance_at_open=10000.0,
            mt5_ticket=12345,
        )

        open_trades = get_open_trades(self.conn)
        self.assertEqual(len(open_trades), 1)
        self.assertEqual(open_trades[0]["mt5_ticket"], 12345)

        close_trade_record(
            self.conn,
            trade_id=trade_id,
            exit_price=1.09300,
            profit_loss=130.0,
            balance_at_close=10130.0,
            close_reason="take_profit",
        )

        open_after = get_open_trades(self.conn)
        self.assertEqual(len(open_after), 0)

        recent = get_recent_trades(self.conn, limit=5)
        self.assertEqual(len(recent), 1)
        self.assertAlmostEqual(recent[0]["profit_loss"], 130.0)
        self.assertEqual(recent[0]["close_reason"], "take_profit")
        self.assertIsNotNone(recent[0]["duration_minutes"])

    def test_stats_calculation(self) -> None:
        for pnl in [100.0, 80.0, -30.0]:
            tid = log_trade(
                self.conn,
                pair="EURUSD",
                direction="buy",
                entry_price=1.08,
                stop_loss=1.07,
                take_profit=1.10,
                position_size=0.10,
                balance_at_open=10000.0,
            )
            close_trade_record(
                self.conn,
                tid,
                exit_price=1.09,
                profit_loss=pnl,
                balance_at_close=10000.0 + pnl,
                close_reason="take_profit" if pnl > 0 else "stop_loss",
            )

        stats = get_stats(self.conn)
        self.assertEqual(stats["total_trades"], 3)
        self.assertEqual(stats["winning_trades"], 2)
        self.assertEqual(stats["losing_trades"], 1)
        self.assertAlmostEqual(stats["win_rate"], 2 / 3)
        self.assertAlmostEqual(stats["total_pnl"], 150.0)

    def test_close_nonexistent_trade_raises(self) -> None:
        with self.assertRaises(ValueError):
            close_trade_record(
                self.conn,
                trade_id=99999,
                exit_price=1.09,
                profit_loss=0.0,
                balance_at_close=10000.0,
                close_reason="manual",
            )


# ---------------------------------------------------------------------------
# Test 10: Position sizing edge cases
# ---------------------------------------------------------------------------


class TestPositionSizing(unittest.TestCase):
    def test_minimum_lot_respected(self) -> None:
        # Tiny balance — should return 0 (below 0.01 minimum).
        lot = calculate_position_size(
            balance=50.0,
            risk_percent=1.0,
            stop_loss_pips=100.0,
            pip_value=10.0,
        )
        self.assertEqual(lot, 0.0)

    def test_zero_stop_loss_pips_returns_zero(self) -> None:
        lot = calculate_position_size(
            balance=10000.0,
            risk_percent=1.0,
            stop_loss_pips=0.0,
            pip_value=10.0,
        )
        self.assertEqual(lot, 0.0)

    def test_lot_size_floors_to_two_decimals(self) -> None:
        lot = calculate_position_size(
            balance=10000.0,
            risk_percent=1.0,
            stop_loss_pips=50.0,
            pip_value=10.0,
        )
        # Verify it's a multiple of 0.01.
        self.assertAlmostEqual(lot, round(lot, 2), places=8)

    def test_higher_balance_gives_larger_lot(self) -> None:
        lot_small = calculate_position_size(5000.0, 1.0, 50.0, 10.0)
        lot_large = calculate_position_size(50000.0, 1.0, 50.0, 10.0)
        self.assertGreater(lot_large, lot_small)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s [%(name)s] %(message)s",
    )
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(Path(__file__).parent), pattern="test_e2e.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    skipped = len(result.skipped)

    print("\n" + "=" * 60)
    print("DRIFT E2E TEST SUMMARY")
    print(f"  Ran:     {total}")
    print(f"  Passed:  {total - failures - skipped}")
    print(f"  Skipped: {skipped}")
    print(f"  Failed:  {failures}")
    if not HAS_PANDAS_TA:
        print("  NOTE: pandas_ta not available — indicator tests were skipped")
    print("=" * 60)

    sys.exit(0 if result.wasSuccessful() else 1)
