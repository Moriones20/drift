"""Tests for per-strategy Telegram control plane (D057 / Step 34).

Covers:
  - /pause daily_lull  → sets strategy_state.paused via set_strategy_paused
  - /pause (no arg)    → sets global state.paused
  - /pause invalid     → replies with list of valid names
  - /resume daily_lull → clears strategy_state.paused
  - /resume (no arg)   → clears global state.paused
  - /strategies        → formats per-strategy desglose (enabled, paused, magic, open trades)
  - /report            → includes BY STRATEGY section when config is present
  - generate_weekly_report → includes per-strategy section when config is given
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import drift.db as db
from drift.config import (
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
from drift.db import get_strategy_state, set_strategy_paused
from drift.report import generate_weekly_report
from drift.telegram_bot import BotState, _make_handlers

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    db.init_db(p)
    return p


def _single_strategy_config(magic_offset: int = 0) -> DriftConfig:
    """Config with one enabled strategy (daily_lull)."""
    params = {f: getattr(StrategyConfig(), f) for f in StrategyConfig.__dataclass_fields__}
    return DriftConfig(
        broker=BrokerConfig(server="demo", login=1, password="x"),
        telegram=TelegramConfig(bot_token="fake:TOKEN", chat_id="123"),
        reports=ReportsConfig(),
        system=SystemConfig(magic_number=234000),
        risk_global=RiskGlobalConfig(),
        strategies={
            "daily_lull": StrategyInstanceConfig(
                name="daily_lull",
                enabled=True,
                magic_offset=magic_offset,
                allocation_pct=100.0,
                pairs=["AUDNZD"],
                risk=StrategyRiskConfig(),
                params=params,
            )
        },
    )


def _two_strategy_config() -> DriftConfig:
    params = {f: getattr(StrategyConfig(), f) for f in StrategyConfig.__dataclass_fields__}
    return DriftConfig(
        broker=BrokerConfig(server="demo", login=1, password="x"),
        telegram=TelegramConfig(bot_token="fake:TOKEN", chat_id="123"),
        reports=ReportsConfig(),
        system=SystemConfig(magic_number=234000),
        risk_global=RiskGlobalConfig(),
        strategies={
            "daily_lull": StrategyInstanceConfig(
                name="daily_lull",
                enabled=True,
                magic_offset=0,
                allocation_pct=60.0,
                pairs=["AUDNZD"],
                risk=StrategyRiskConfig(),
                params=params,
            ),
            "scalper": StrategyInstanceConfig(
                name="scalper",
                enabled=True,
                magic_offset=1,
                allocation_pct=40.0,
                pairs=["EURCHF"],
                risk=StrategyRiskConfig(),
                params=params,
            ),
        },
    )


def _make_update(chat_id: str = "123", args: list[str] | None = None) -> tuple:
    """Return (update_mock, context_mock) mimicking a Telegram command."""
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = args or []
    return update, context


def _run(coro):
    """Run a coroutine in a fresh event loop."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# /pause with strategy name → sets strategy_state.paused
# ---------------------------------------------------------------------------


class TestPauseWithStrategyName:
    def test_pauses_strategy_in_db(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["daily_lull"])

        with patch("drift.telegram_bot.get_balance", return_value=1000.0):
            _run(handlers["pause"](update, context))

        # Check DB
        with db.get_connection(db_path) as conn:
            row = get_strategy_state(conn, "daily_lull")
        assert row is not None
        assert bool(row["paused"]) is True

    def test_global_state_not_affected(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        state.paused = False
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["daily_lull"])

        with patch("drift.telegram_bot.get_balance", return_value=1000.0):
            _run(handlers["pause"](update, context))

        assert state.paused is False  # global state untouched

    def test_already_paused_replies_already_paused(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        # Pre-pause the strategy
        with db.get_connection(db_path) as conn:
            set_strategy_paused(conn, "daily_lull", True)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["daily_lull"])
        _run(handlers["pause"](update, context))

        call_args = update.message.reply_text.call_args
        assert "already paused" in call_args[0][0].lower()

    def test_reply_confirms_pause(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["daily_lull"])
        _run(handlers["pause"](update, context))

        call_args = update.message.reply_text.call_args
        assert "daily_lull" in call_args[0][0]


# ---------------------------------------------------------------------------
# /pause without argument → global pause
# ---------------------------------------------------------------------------


class TestPauseGlobal:
    def test_sets_global_state_paused(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()  # no args
        _run(handlers["pause"](update, context))
        assert state.paused is True

    def test_already_paused_globally_replies(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        state.paused = True
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()
        _run(handlers["pause"](update, context))
        call_args = update.message.reply_text.call_args
        assert "already paused" in call_args[0][0].lower()


# ---------------------------------------------------------------------------
# /pause with invalid strategy name
# ---------------------------------------------------------------------------


class TestPauseInvalidName:
    def test_replies_with_valid_names(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["does_not_exist"])
        _run(handlers["pause"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "daily_lull" in reply_text
        assert "Unknown strategy" in reply_text or "does_not_exist" in reply_text

    def test_db_not_modified_for_invalid_name(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["ghost_strategy"])
        _run(handlers["pause"](update, context))

        with db.get_connection(db_path) as conn:
            row = get_strategy_state(conn, "ghost_strategy")
        assert row is None  # no row created for invalid name


# ---------------------------------------------------------------------------
# /resume with strategy name
# ---------------------------------------------------------------------------


class TestResumeWithStrategyName:
    def test_resumes_strategy_in_db(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        # Pre-pause
        with db.get_connection(db_path) as conn:
            set_strategy_paused(conn, "daily_lull", True)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["daily_lull"])
        _run(handlers["resume"](update, context))

        with db.get_connection(db_path) as conn:
            row = get_strategy_state(conn, "daily_lull")
        assert row is not None
        assert bool(row["paused"]) is False

    def test_invalid_name_replies_with_valid_names(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["bad_name"])
        _run(handlers["resume"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "daily_lull" in reply_text


# ---------------------------------------------------------------------------
# /strategies — formats per-strategy breakdown
# ---------------------------------------------------------------------------


class TestStrategiesCommand:
    def test_lists_all_strategies(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _two_strategy_config()
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()
        _run(handlers["strategies"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "daily_lull" in reply_text
        assert "scalper" in reply_text

    def test_shows_magic_numbers(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _two_strategy_config()
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()
        _run(handlers["strategies"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        # daily_lull: magic 234000 + 0 = 234000; scalper: 234000 + 1 = 234001
        assert "234000" in reply_text
        assert "234001" in reply_text

    def test_shows_paused_label(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        # Pause daily_lull
        with db.get_connection(db_path) as conn:
            set_strategy_paused(conn, "daily_lull", True)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()
        _run(handlers["strategies"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "paused" in reply_text.lower()

    def test_shows_allocation_pct(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _two_strategy_config()
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()
        _run(handlers["strategies"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "60" in reply_text  # daily_lull allocation_pct
        assert "40" in reply_text  # scalper allocation_pct


# ---------------------------------------------------------------------------
# /report — includes BY STRATEGY section when config provided
# ---------------------------------------------------------------------------


class TestReportWithStrategyBreakdown:
    def test_report_includes_strategy_section(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()

        with patch("drift.telegram_bot.get_balance", return_value=1000.0):
            handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()

        with patch("drift.telegram_bot.get_balance", return_value=1000.0):
            _run(handlers["report"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "BY STRATEGY" in reply_text
        assert "daily_lull" in reply_text

    def test_report_without_config_has_no_strategy_section(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config=None)
        update, context = _make_update()

        with patch("drift.telegram_bot.get_balance", return_value=1000.0):
            _run(handlers["report"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "BY STRATEGY" not in reply_text


# ---------------------------------------------------------------------------
# generate_weekly_report — per-strategy section
# ---------------------------------------------------------------------------


class TestWeeklyReportStrategySection:
    def test_report_includes_strategy_section_with_config(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()

        with db.get_connection(db_path) as conn:
            report = generate_weekly_report(
                conn,
                current_balance=1000.0,
                peak_balance=1050.0,
                config=config,
            )

        assert "BY STRATEGY" in report
        assert "daily_lull" in report

    def test_report_no_config_has_no_strategy_section(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)

        with db.get_connection(db_path) as conn:
            report = generate_weekly_report(
                conn,
                current_balance=1000.0,
                peak_balance=1050.0,
                config=None,
            )

        assert "BY STRATEGY" not in report

    def test_report_with_trades_per_strategy(self, tmp_path: Path) -> None:
        """Trades attributed to a strategy appear in that strategy's section."""
        db_path = _make_db(tmp_path)
        config = _two_strategy_config()

        # Insert one closed trade per strategy within the last 7 days
        with db.get_connection(db_path) as conn:
            now = datetime.now(timezone.utc)
            recent = (now - timedelta(days=1)).isoformat()

            conn.execute(
                "INSERT INTO trades (strategy, pair, direction, entry_price, stop_loss,"
                " take_profit, position_size, balance_at_open, opened_at,"
                " closed_at, exit_price, profit_loss, balance_at_close,"
                " close_reason, duration_minutes)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "daily_lull",
                    "AUDNZD",
                    "buy",
                    1.0700,
                    1.0650,
                    1.0800,
                    0.01,
                    1000.0,
                    recent,
                    recent,
                    1.0800,
                    10.0,
                    1010.0,
                    "take_profit",
                    30,
                ),
            )
            conn.execute(
                "INSERT INTO trades (strategy, pair, direction, entry_price, stop_loss,"
                " take_profit, position_size, balance_at_open, opened_at,"
                " closed_at, exit_price, profit_loss, balance_at_close,"
                " close_reason, duration_minutes)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "scalper",
                    "EURCHF",
                    "sell",
                    0.9300,
                    0.9350,
                    0.9200,
                    0.01,
                    1000.0,
                    recent,
                    recent,
                    0.9200,
                    15.0,
                    1015.0,
                    "take_profit",
                    20,
                ),
            )
            conn.commit()
            report = generate_weekly_report(
                conn,
                current_balance=1000.0,
                peak_balance=1050.0,
                config=config,
            )

        assert "daily_lull" in report
        assert "scalper" in report


# ---------------------------------------------------------------------------
# /resume <strategy> resets peak (D063)
# ---------------------------------------------------------------------------


class TestResumeResetsStrategyPeak:
    """After /resume <strategy>, the stored peak_equity equals the current equity
    so the per-strategy drawdown is ~0 and the monitor will not immediately re-pause."""

    def test_peak_is_reset_to_current_equity_on_resume(self, tmp_path: Path) -> None:
        # Arrange: strategy is paused with an old high peak.
        # baseline_capital seeded so that equity = 850 (drawdown from peak 1000).
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()

        with db.get_connection(db_path) as conn:
            # Seed baseline = 1000, set peak = 1000 (high-water before drawdown)
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.set_strategy_paused(conn, "daily_lull", True)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["daily_lull"])

        # No open MT5 positions → floating = 0.  get_open_positions returns []
        # when MT5 is not connected (returns None → []).
        with patch("drift.telegram_bot.get_open_positions", return_value=[]):
            _run(handlers["resume"](update, context))

        with db.get_connection(db_path) as conn:
            row = db.get_strategy_state(conn, "daily_lull")

        # Equity = baseline(1000) + realized(0) + floating(0) = 1000
        assert row is not None
        assert row["peak_equity"] == pytest.approx(1000.0)
        assert bool(row["paused"]) is False

    def test_drawdown_is_zero_after_resume(self, tmp_path: Path) -> None:
        # After resume with no positions, peak == equity == 1000 → drawdown 0%.
        from drift.risk import check_strategy_drawdown

        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()

        with db.get_connection(db_path) as conn:
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.set_strategy_paused(conn, "daily_lull", True)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["daily_lull"])

        with patch("drift.telegram_bot.get_open_positions", return_value=[]):
            _run(handlers["resume"](update, context))

        with db.get_connection(db_path) as conn:
            row = db.get_strategy_state(conn, "daily_lull")

        ok, reason = check_strategy_drawdown(1000.0, row["peak_equity"], 10.0)
        assert ok is True  # monitor would NOT re-pause immediately
        assert reason == ""

    def test_resume_without_baseline_skips_peak_reset(self, tmp_path: Path) -> None:
        # If baseline_capital is NULL (strategy never evaluated), resume just
        # clears the pause without touching the peak.
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()

        with db.get_connection(db_path) as conn:
            # Only set the pause flag; leave baseline_capital NULL.
            db.set_strategy_paused(conn, "daily_lull", True)
            # Also give it an arbitrary peak (upsert_strategy_peak updates only
            # peak_equity on conflict — the paused flag is left untouched).
            db.upsert_strategy_peak(conn, "daily_lull", 777.0)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["daily_lull"])

        with patch("drift.telegram_bot.get_open_positions", return_value=[]):
            _run(handlers["resume"](update, context))

        with db.get_connection(db_path) as conn:
            row = db.get_strategy_state(conn, "daily_lull")

        # Pause cleared, peak unchanged (baseline was None)
        assert bool(row["paused"]) is False
        assert row["peak_equity"] == pytest.approx(777.0)

    def test_reply_mentions_drawdown_window_reset(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()

        with db.get_connection(db_path) as conn:
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.set_strategy_paused(conn, "daily_lull", True)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update(args=["daily_lull"])

        with patch("drift.telegram_bot.get_open_positions", return_value=[]):
            _run(handlers["resume"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "drawdown" in reply_text.lower()

    def test_global_resume_does_not_reset_peak(self, tmp_path: Path) -> None:
        # The global /resume (no argument) must NOT touch strategy peak_equity
        # (D063 defers global-peak reset).
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        state.paused = True

        with db.get_connection(db_path) as conn:
            db.upsert_strategy_peak(conn, "daily_lull", 1234.0)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()  # no args

        _run(handlers["resume"](update, context))

        with db.get_connection(db_path) as conn:
            row = db.get_strategy_state(conn, "daily_lull")

        # Global resume cleared global state
        assert state.paused is False
        # Strategy peak must be untouched
        assert row["peak_equity"] == pytest.approx(1234.0)


# ---------------------------------------------------------------------------
# Step 39 — canonical equity display (D062 / audit #4)
# ---------------------------------------------------------------------------


class TestStrategyEquityInfo:
    """_strategy_equity_info returns the canonical (equity, peak, dd_pct) triple."""

    def test_returns_none_triple_when_no_state_row(self, tmp_path: Path) -> None:
        from drift.telegram_bot import _strategy_equity_info

        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        with db.get_connection(db_path) as conn:
            equity, peak, dd_pct = _strategy_equity_info(conn, config, "daily_lull", None)
        assert equity is None
        assert peak is None
        assert dd_pct is None

    def test_returns_none_triple_when_baseline_is_null(self, tmp_path: Path) -> None:
        from drift.telegram_bot import _strategy_equity_info

        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        with db.get_connection(db_path) as conn:
            # Create a row without baseline_capital (just a peak via upsert)
            db.upsert_strategy_peak(conn, "daily_lull", 999.0)
            row = db.get_strategy_state(conn, "daily_lull")
            equity, peak, dd_pct = _strategy_equity_info(conn, config, "daily_lull", row)
        assert equity is None
        assert peak is None
        assert dd_pct is None

    def test_canonical_drawdown_formula(self, tmp_path: Path) -> None:
        """equity = baseline + realized + floating; dd = (peak - equity)/peak * 100."""
        from drift.telegram_bot import _strategy_equity_info

        db_path = _make_db(tmp_path)
        config = _single_strategy_config()

        # Seed: baseline_capital=800, peak_equity=1000
        with db.get_connection(db_path) as conn:
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            # Override peak to 1000 to simulate drawdown from there
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)
            row = db.get_strategy_state(conn, "daily_lull")

            # No closed trades → realized=0; mock no open positions → floating=0
            with patch("drift.telegram_bot.get_open_positions", return_value=[]):
                equity, peak, dd_pct = _strategy_equity_info(conn, config, "daily_lull", row)

        # baseline=1000 (seeded with balance=1000, realized=0 at seed),
        # equity = 1000 + 0 + 0 = 1000, peak = 1000 → dd = 0
        assert equity == pytest.approx(1000.0)
        assert peak == pytest.approx(1000.0)
        assert dd_pct == pytest.approx(0.0)

    def test_drawdown_with_floating_loss(self, tmp_path: Path) -> None:
        """Floating loss is reflected in equity and drawdown."""
        from drift.telegram_bot import _strategy_equity_info

        db_path = _make_db(tmp_path)
        config = _single_strategy_config()

        with db.get_connection(db_path) as conn:
            # baseline=1000, peak=1000
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)
            row = db.get_strategy_state(conn, "daily_lull")

            # Simulate $50 floating loss (open position with profit=-50)
            fake_positions = [{"profit": -50.0}]
            with patch("drift.telegram_bot.get_open_positions", return_value=fake_positions):
                equity, peak, dd_pct = _strategy_equity_info(conn, config, "daily_lull", row)

        # equity = 1000 + 0 + (-50) = 950, peak=1000 → dd=5%
        assert equity == pytest.approx(950.0)
        assert peak == pytest.approx(1000.0)
        assert dd_pct == pytest.approx(5.0)

    def test_drawdown_equals_canonical_formula(self, tmp_path: Path) -> None:
        """dd_pct must equal (peak - equity)/peak*100, NOT (peak - account_balance)/peak."""
        from drift.telegram_bot import _strategy_equity_info

        db_path = _make_db(tmp_path)
        config = _single_strategy_config()

        with db.get_connection(db_path) as conn:
            # baseline=1000, peak=1100 (strategy was up at some point)
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.upsert_strategy_peak(conn, "daily_lull", 1100.0)
            row = db.get_strategy_state(conn, "daily_lull")

            # Realized +20, floating -30 → equity = 1000 + 20 - 30 = 990
            with db.get_connection(db_path) as conn2:
                now = datetime.now(timezone.utc)
                conn2.execute(
                    "INSERT INTO trades (strategy, pair, direction, entry_price,"
                    " stop_loss, take_profit, position_size, balance_at_open,"
                    " opened_at, closed_at, exit_price, profit_loss,"
                    " balance_at_close, close_reason, duration_minutes)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "daily_lull",
                        "AUDNZD",
                        "buy",
                        1.07,
                        1.065,
                        1.08,
                        0.01,
                        1000.0,
                        now.isoformat(),
                        now.isoformat(),
                        1.08,
                        20.0,
                        1020.0,
                        "take_profit",
                        30,
                    ),
                )
                conn2.commit()

                fake_positions = [{"profit": -30.0}]
                with patch("drift.telegram_bot.get_open_positions", return_value=fake_positions):
                    equity, peak, dd_pct = _strategy_equity_info(conn2, config, "daily_lull", row)

        expected_equity = 1000.0 + 20.0 + (-30.0)  # = 990
        expected_dd = (1100.0 - expected_equity) / 1100.0 * 100  # = 10/1100 * 100 ≈ 9.09%
        assert equity == pytest.approx(expected_equity)
        assert dd_pct == pytest.approx(expected_dd, rel=1e-4)

    def test_at_peak_dd_is_zero(self, tmp_path: Path) -> None:
        """When equity equals or exceeds peak, drawdown is 0.0 (clamped)."""
        from drift.telegram_bot import _strategy_equity_info

        db_path = _make_db(tmp_path)
        config = _single_strategy_config()

        with db.get_connection(db_path) as conn:
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.upsert_strategy_peak(conn, "daily_lull", 900.0)  # peak below current equity
            row = db.get_strategy_state(conn, "daily_lull")

            with patch("drift.telegram_bot.get_open_positions", return_value=[]):
                equity, peak, dd_pct = _strategy_equity_info(conn, config, "daily_lull", row)

        # equity=1000 > peak=900 → strategy_drawdown returns 0.0
        assert equity == pytest.approx(1000.0)
        assert dd_pct == pytest.approx(0.0)

    def test_mt5_failure_uses_zero_floating(self, tmp_path: Path) -> None:
        """A failure fetching positions degrades gracefully (floating=0)."""
        from drift.telegram_bot import _strategy_equity_info

        db_path = _make_db(tmp_path)
        config = _single_strategy_config()

        with db.get_connection(db_path) as conn:
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)
            row = db.get_strategy_state(conn, "daily_lull")

            with patch(
                "drift.telegram_bot.get_open_positions", side_effect=RuntimeError("MT5 down")
            ):
                equity, peak, dd_pct = _strategy_equity_info(conn, config, "daily_lull", row)

        # floating defaults to 0 on failure → equity = 1000
        assert equity == pytest.approx(1000.0)
        assert dd_pct == pytest.approx(0.0)


class TestBalanceCommandEquityDisplay:
    """/balance per-strategy section uses canonical drawdown (D062)."""

    def test_balance_shows_no_data_when_baseline_null(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()

        with (
            patch("drift.telegram_bot.get_balance", return_value=1000.0),
            patch("drift.telegram_bot.get_open_positions", return_value=[]),
        ):
            _run(handlers["balance"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        # No baseline seeded → "no data"
        assert "no data" in reply_text

    def test_balance_shows_at_peak_when_dd_zero(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()

        with db.get_connection(db_path) as conn:
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()

        with (
            patch("drift.telegram_bot.get_balance", return_value=1000.0),
            patch("drift.telegram_bot.get_open_positions", return_value=[]),
        ):
            _run(handlers["balance"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "At peak" in reply_text

    def test_balance_shows_drawdown_pct(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()

        with db.get_connection(db_path) as conn:
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()

        # floating loss of $100 → equity=900, peak=1000, dd=10%
        with (
            patch("drift.telegram_bot.get_balance", return_value=900.0),
            patch("drift.telegram_bot.get_open_positions", return_value=[{"profit": -100.0}]),
        ):
            _run(handlers["balance"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "-10.0%" in reply_text

    def test_balance_drawdown_not_based_on_raw_balance(self, tmp_path: Path) -> None:
        """Verify that the displayed drawdown is NOT the old (peak-balance)/peak formula."""
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()

        # baseline=800 (strategy was already in drawdown at seed time), peak=1000
        with db.get_connection(db_path) as conn:
            # seed: balance=1000, realized=200 → baseline = 1000 - 200 = 800
            conn.execute(
                "INSERT INTO trades (strategy, pair, direction, entry_price,"
                " stop_loss, take_profit, position_size, balance_at_open,"
                " opened_at, closed_at, exit_price, profit_loss,"
                " balance_at_close, close_reason, duration_minutes)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "daily_lull",
                    "AUDNZD",
                    "buy",
                    1.07,
                    1.065,
                    1.08,
                    0.01,
                    1000.0,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    1.08,
                    200.0,
                    1200.0,
                    "take_profit",
                    30,
                ),
            )
            conn.commit()
            db.seed_strategy_baseline(conn, "daily_lull", 1200.0, 1200.0)
            # Peak was recorded at 1000 notional (baseline=800, realized=200 → equity=1000)
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()

        # Account balance is $1200 (the raw MT5 balance), floating=0
        # OLD formula: (1000 - 1200)/1000 → negative → 0% (wrong, coherent with nothing)
        # NEW formula: equity = 800 + 200 + 0 = 1000 = peak → dd=0%
        with (
            patch("drift.telegram_bot.get_balance", return_value=1200.0),
            patch("drift.telegram_bot.get_open_positions", return_value=[]),
        ):
            _run(handlers["balance"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        # The canonical answer is "At peak" (equity == peak == 1000)
        assert "At peak" in reply_text


class TestStrategiesCommandEquityDisplay:
    """/strategies per-strategy section uses canonical drawdown (D062)."""

    def test_strategies_shows_no_data_when_baseline_null(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()
        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()

        with patch("drift.telegram_bot.get_open_positions", return_value=[]):
            _run(handlers["strategies"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "no data" in reply_text

    def test_strategies_shows_at_peak_when_dd_zero(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()

        with db.get_connection(db_path) as conn:
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()

        with patch("drift.telegram_bot.get_open_positions", return_value=[]):
            _run(handlers["strategies"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "At peak" in reply_text

    def test_strategies_shows_drawdown_pct(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()

        with db.get_connection(db_path) as conn:
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()

        # floating loss of $50 → equity=950, peak=1000 → dd=5%
        with patch("drift.telegram_bot.get_open_positions", return_value=[{"profit": -50.0}]):
            _run(handlers["strategies"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        assert "-5.0% DD" in reply_text

    def test_strategies_shows_live_equity(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        config = _single_strategy_config()
        state = BotState()

        with db.get_connection(db_path) as conn:
            db.seed_strategy_baseline(conn, "daily_lull", 1000.0, 1000.0)
            db.upsert_strategy_peak(conn, "daily_lull", 1000.0)

        handlers = _make_handlers("123", state, db_path, config)
        update, context = _make_update()

        with patch("drift.telegram_bot.get_open_positions", return_value=[]):
            _run(handlers["strategies"](update, context))

        reply_text = update.message.reply_text.call_args[0][0]
        # The live equity ($1000.00) should appear in the output
        assert "1000.00" in reply_text
