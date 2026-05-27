from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from drift.strategy import Signal

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "drift.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
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
        'manual', 'drawdown_pause', 'friday_close'
    )),
    duration_minutes INTEGER,
    mt5_ticket INTEGER
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    h4_candle_time TEXT NOT NULL,
    ema_50 REAL,
    ema_200 REAL,
    trend_direction TEXT CHECK(trend_direction IN ('bullish', 'bearish', 'none')),
    macd_value REAL,
    macd_signal REAL,
    macd_histogram REAL,
    atr_value REAL,
    decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
    reason TEXT NOT NULL,
    trade_id INTEGER REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS bot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_at TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'start', 'stop', 'pause', 'resume', 'error', 'reconnect', 'drawdown_alert'
    )),
    detail TEXT,
    balance REAL
);

CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair);
CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_at);
CREATE INDEX IF NOT EXISTS idx_signals_pair ON signals(pair);
CREATE INDEX IF NOT EXISTS idx_signals_analyzed ON signals(analyzed_at);
CREATE INDEX IF NOT EXISTS idx_signals_decision ON signals(decision);
CREATE INDEX IF NOT EXISTS idx_events_type ON bot_events(event_type);
"""


def _resolve_path(db_path: str | Path | None) -> Path:
    if db_path is None:
        return _DEFAULT_DB_PATH
    return Path(db_path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def init_db(db_path: str | Path | None = None) -> None:
    """Create tables and indexes if they don't exist.

    Creates the parent directory for the database file if needed.
    """
    path = _resolve_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        logger.info("Database initialized at %s", path)
    finally:
        conn.close()


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Return a connection with row_factory set to sqlite3.Row."""
    path = _resolve_path(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Trade CRUD
# ---------------------------------------------------------------------------


def log_trade(
    conn: sqlite3.Connection,
    pair: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    position_size: float,
    balance_at_open: float,
    mt5_ticket: int | None = None,
) -> int:
    """Insert a new open trade and return its ID."""
    opened_at = _utc_now()
    cursor = conn.execute(
        """
        INSERT INTO trades (
            pair, direction, entry_price, stop_loss, take_profit,
            position_size, balance_at_open, opened_at, mt5_ticket
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pair,
            direction,
            entry_price,
            stop_loss,
            take_profit,
            position_size,
            balance_at_open,
            opened_at,
            mt5_ticket,
        ),
    )
    conn.commit()
    trade_id = cursor.lastrowid
    logger.debug("Logged new trade id=%s pair=%s direction=%s", trade_id, pair, direction)
    return trade_id


def close_trade_record(
    conn: sqlite3.Connection,
    trade_id: int,
    exit_price: float,
    profit_loss: float,
    balance_at_close: float,
    close_reason: str,
) -> None:
    """Update a trade with close information and calculate duration."""
    closed_at = _utc_now()

    row = conn.execute("SELECT opened_at FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if row is None:
        raise ValueError(f"Trade id={trade_id} not found")

    opened_at_str: str = row["opened_at"]
    opened_dt = datetime.fromisoformat(opened_at_str)
    closed_dt = datetime.fromisoformat(closed_at)
    duration_minutes = int((closed_dt - opened_dt).total_seconds() // 60)

    conn.execute(
        """
        UPDATE trades
        SET exit_price = ?,
            profit_loss = ?,
            balance_at_close = ?,
            closed_at = ?,
            close_reason = ?,
            duration_minutes = ?
        WHERE id = ?
        """,
        (
            exit_price,
            profit_loss,
            balance_at_close,
            closed_at,
            close_reason,
            duration_minutes,
            trade_id,
        ),
    )
    conn.commit()
    logger.debug(
        "Closed trade id=%s reason=%s pnl=%s duration=%dm",
        trade_id,
        close_reason,
        profit_loss,
        duration_minutes,
    )


def get_open_trades(conn: sqlite3.Connection) -> list[dict]:
    """Return all trades where closed_at IS NULL."""
    rows = conn.execute(
        "SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at ASC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_recent_trades(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Return the last N closed trades ordered by closed_at DESC."""
    rows = conn.execute(
        "SELECT * FROM trades WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_trade_by_ticket(conn: sqlite3.Connection, mt5_ticket: int) -> dict | None:
    """Find a trade by its MT5 ticket number."""
    row = conn.execute("SELECT * FROM trades WHERE mt5_ticket = ?", (mt5_ticket,)).fetchone()
    return _row_to_dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Signal CRUD
# ---------------------------------------------------------------------------


def log_signal(
    conn: sqlite3.Connection,
    signal: Signal,
    trade_id: int | None = None,
) -> int:
    """Insert a signal record derived from a Signal dataclass. Returns signal ID."""
    decision = "accepted" if signal.action != "none" else "rejected"
    analyzed_at = signal.timestamp.isoformat()
    h4_candle_time = signal.h4_candle_time.isoformat()

    cursor = conn.execute(
        """
        INSERT INTO signals (
            pair, analyzed_at, h4_candle_time,
            ema_50, ema_200, trend_direction,
            macd_value, macd_signal, macd_histogram, atr_value,
            decision, reason, trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal.pair,
            analyzed_at,
            h4_candle_time,
            signal.ema_fast,
            signal.ema_slow,
            signal.trend_direction,
            signal.macd_value,
            signal.macd_signal,
            signal.macd_histogram,
            signal.atr_value,
            decision,
            signal.reason,
            trade_id,
        ),
    )
    conn.commit()
    signal_id = cursor.lastrowid
    logger.debug(
        "Logged signal id=%s pair=%s decision=%s action=%s",
        signal_id,
        signal.pair,
        decision,
        signal.action,
    )
    return signal_id


# ---------------------------------------------------------------------------
# Event CRUD
# ---------------------------------------------------------------------------


def log_event(
    conn: sqlite3.Connection,
    event_type: str,
    detail: str | None = None,
    balance: float | None = None,
) -> int:
    """Insert a bot event and return its ID."""
    event_at = _utc_now()
    cursor = conn.execute(
        "INSERT INTO bot_events (event_at, event_type, detail, balance) VALUES (?, ?, ?, ?)",
        (event_at, event_type, detail, balance),
    )
    conn.commit()
    event_id = cursor.lastrowid
    logger.debug("Logged event id=%s type=%s", event_id, event_type)
    return event_id


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def get_stats(conn: sqlite3.Connection) -> dict:
    """Return aggregate statistics for all recorded trades."""
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_trades,
            SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) AS winning_trades,
            SUM(CASE WHEN profit_loss <= 0 THEN 1 ELSE 0 END) AS losing_trades,
            SUM(profit_loss) AS total_pnl,
            MAX(balance_at_open) AS peak_balance,
            AVG(duration_minutes) AS avg_trade_duration_minutes
        FROM trades
        WHERE closed_at IS NOT NULL
        """
    ).fetchone()

    total = row["total_trades"] or 0
    winning = row["winning_trades"] or 0
    losing = row["losing_trades"] or 0
    win_rate = (winning / total) if total > 0 else 0.0

    return {
        "total_trades": total,
        "winning_trades": winning,
        "losing_trades": losing,
        "win_rate": win_rate,
        "total_pnl": row["total_pnl"] or 0.0,
        "peak_balance": row["peak_balance"] or 0.0,
        "avg_trade_duration_minutes": row["avg_trade_duration_minutes"] or 0.0,
    }
