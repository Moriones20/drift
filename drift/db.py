from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from drift.risk import check_strategy_drawdown, seed_baseline_capital, strategy_equity

if TYPE_CHECKING:
    from drift.strategies.base import Signal

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "drift.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL DEFAULT 'daily_lull',
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

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL DEFAULT 'daily_lull',
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

CREATE TABLE IF NOT EXISTS bot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_at TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'start', 'stop', 'pause', 'resume', 'error', 'reconnect', 'drawdown_alert',
        'peak_balance'
    )),
    strategy TEXT,
    detail TEXT,
    balance REAL
);

CREATE TABLE IF NOT EXISTS strategy_state (
    strategy         TEXT PRIMARY KEY,
    peak_equity      REAL NOT NULL,
    paused           INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL,
    baseline_capital REAL
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


def _migrate_signals_table(conn: sqlite3.Connection) -> None:
    """Migrate signals table through successive schema versions.

    v1 → v2: macd_value column existed        → renamed to signals_v1
    v2 → v3: ema_20/ema_gap_pct existed       → renamed to signals_v2
    v3 → v4: bb_upper existed (mean-reversion) → renamed to signals_v3
             Daily Lull Scalper schema created (m15_candle_time, range_high,
             range_low, range_atr_ratio, h4_adx; bb_upper/bb_middle/bb_lower/adx removed)
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}

    # v1 migration: MACD-era schema
    if "macd_value" in cols:
        logger.info("Migrating signals table v1→v2: renaming to signals_v1")
        conn.execute("ALTER TABLE signals RENAME TO signals_v1")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                h4_candle_time TEXT NOT NULL,
                ema_50 REAL,
                ema_200 REAL,
                trend_direction TEXT CHECK(trend_direction IN ('bullish', 'bearish', 'none')),
                ema_20 REAL,
                rsi REAL,
                adx REAL,
                ema_gap_pct REAL,
                atr_value REAL,
                decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
                reason TEXT NOT NULL,
                trade_id INTEGER REFERENCES trades(id)
            );
            CREATE INDEX IF NOT EXISTS idx_signals_pair ON signals(pair);
            CREATE INDEX IF NOT EXISTS idx_signals_analyzed ON signals(analyzed_at);
            CREATE INDEX IF NOT EXISTS idx_signals_decision ON signals(decision);
            """
        )
        conn.commit()
        logger.info("v1→v2 migration complete — old data preserved in signals_v1")
        # Refresh cols for v2→v3 check
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}

    # v2 migration: EMA pullback / trend-following era schema → mean reversion schema
    if "ema_20" in cols or "ema_gap_pct" in cols:
        logger.info("Migrating signals table v2→v3: renaming to signals_v2")
        conn.execute("ALTER TABLE signals RENAME TO signals_v2")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                h4_candle_time TEXT NOT NULL,
                bb_upper REAL,
                bb_middle REAL,
                bb_lower REAL,
                rsi REAL,
                adx REAL,
                atr_value REAL,
                decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
                reason TEXT NOT NULL,
                trade_id INTEGER REFERENCES trades(id)
            );
            CREATE INDEX IF NOT EXISTS idx_signals_pair ON signals(pair);
            CREATE INDEX IF NOT EXISTS idx_signals_analyzed ON signals(analyzed_at);
            CREATE INDEX IF NOT EXISTS idx_signals_decision ON signals(decision);
            """
        )
        conn.commit()
        logger.info("v2→v3 migration complete — old data preserved in signals_v2")
        # Refresh cols for v3→v4 check
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}

    # v3 migration: mean-reversion (BB+RSI) schema → Daily Lull Scalper schema
    if "bb_upper" in cols or ("m15_candle_time" not in cols and "h4_adx" not in cols):
        # Guard: if signals_v3 already exists, we already ran this migration once.
        already_done = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signals_v3'"
        ).fetchone()
        if already_done is not None:
            logger.debug("v3→v4 migration already applied — skipping")
            return
        logger.info("Migrating signals table v3→v4: renaming to signals_v3")
        conn.execute("ALTER TABLE signals RENAME TO signals_v3")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
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
            CREATE INDEX IF NOT EXISTS idx_signals_pair ON signals(pair);
            CREATE INDEX IF NOT EXISTS idx_signals_analyzed ON signals(analyzed_at);
            CREATE INDEX IF NOT EXISTS idx_signals_decision ON signals(decision);
            """
        )
        conn.commit()
        logger.info("v3→v4 migration complete — old data preserved in signals_v3")


def _migrate_trades_table(conn: sqlite3.Connection) -> None:
    """Migrate trades table to replace the friday_close CHECK with session_close.

    SQLite does not support ALTER TABLE ... ALTER CONSTRAINT, so a guarded rebuild
    is used: create a new table with the correct CHECK, copy all rows, drop the old
    table, and rename the new one. Recreate the indexes afterward.

    The migration is detected by reading the CREATE TABLE statement from
    sqlite_master and checking for the string 'friday_close'. It is idempotent:
    if that string is not present the function returns immediately without touching
    the table.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='trades'"
    ).fetchone()
    if row is None:
        # Table does not exist yet — the schema script will create it correctly.
        return
    if "friday_close" not in (row[0] or ""):
        logger.debug("Trades table migration not needed — skipping")
        return

    logger.info("Migrating trades table: replacing friday_close with session_close in CHECK")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trades_new (
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

        INSERT INTO trades_new
            SELECT id, pair, direction, entry_price, exit_price, stop_loss, take_profit,
                   position_size, profit_loss, balance_at_open, balance_at_close,
                   opened_at, closed_at,
                   CASE WHEN close_reason = 'friday_close' THEN 'session_close'
                        ELSE close_reason END,
                   duration_minutes, mt5_ticket
            FROM trades;

        DROP TABLE trades;

        ALTER TABLE trades_new RENAME TO trades;

        CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair);
        CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_at);
        """
    )
    conn.commit()
    logger.info("Trades table migration complete")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of column names for an existing table."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _object_exists(conn: sqlite3.Connection, kind: str, name: str) -> bool:
    """Return True if a table or index with the given name exists in sqlite_master."""
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type=? AND name=?", (kind, name)).fetchone()
        is not None
    )


def _migrate_strategy_columns(conn: sqlite3.Connection) -> None:
    """Add per-strategy attribution columns to existing tables (D056).

    Idempotent: checks PRAGMA table_info / sqlite_master before each ALTER so
    running init_db multiple times over an existing DB is safe.

    trades     → ADD COLUMN strategy TEXT NOT NULL DEFAULT 'daily_lull'
    signals    → ADD COLUMN strategy TEXT NOT NULL DEFAULT 'daily_lull'
    bot_events → ADD COLUMN strategy TEXT  (NULL allowed — NULL = global event)

    Also creates strategy_state table and idx_trades_strategy if absent.
    All DDL is committed in a single transaction at the end.
    """
    changed = False

    # --- trades ---
    if "strategy" not in _table_columns(conn, "trades"):
        logger.info("Migration D056: adding strategy column to trades")
        conn.execute("ALTER TABLE trades ADD COLUMN strategy TEXT NOT NULL DEFAULT 'daily_lull'")
        changed = True

    # --- signals ---
    if "strategy" not in _table_columns(conn, "signals"):
        logger.info("Migration D056: adding strategy column to signals")
        conn.execute("ALTER TABLE signals ADD COLUMN strategy TEXT NOT NULL DEFAULT 'daily_lull'")
        changed = True

    # --- bot_events ---
    if "strategy" not in _table_columns(conn, "bot_events"):
        logger.info("Migration D056: adding strategy column to bot_events")
        conn.execute("ALTER TABLE bot_events ADD COLUMN strategy TEXT")
        changed = True

    # --- strategy_state table ---
    if not _object_exists(conn, "table", "strategy_state"):
        logger.info("Migration D056: creating strategy_state table")
        conn.execute(
            """
            CREATE TABLE strategy_state (
                strategy         TEXT PRIMARY KEY,
                peak_equity      REAL NOT NULL,
                paused           INTEGER NOT NULL DEFAULT 0,
                updated_at       TEXT NOT NULL,
                baseline_capital REAL
            )
            """
        )
        changed = True

    # --- strategy_state.baseline_capital (D062) ---
    # NULL = not seeded yet; seeded once by the canonical equity helper.
    if "baseline_capital" not in _table_columns(conn, "strategy_state"):
        logger.info("Migration D062: adding baseline_capital column to strategy_state")
        conn.execute("ALTER TABLE strategy_state ADD COLUMN baseline_capital REAL")
        changed = True

    # --- idx_trades_strategy ---
    if not _object_exists(conn, "index", "idx_trades_strategy"):
        logger.info("Migration D056: creating idx_trades_strategy")
        conn.execute("CREATE INDEX idx_trades_strategy ON trades(strategy)")
        changed = True

    if changed:
        conn.commit()


def init_db(db_path: str | Path | None = None) -> None:
    """Create tables and indexes if they don't exist.

    Creates the parent directory for the database file if needed.
    Migrates the trades table if the old friday_close CHECK is detected.
    Migrates the signals table through v1→v2→v3→v4 if an older schema is detected.
    Adds per-strategy attribution columns and strategy_state table (D056).
    """
    path = _resolve_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row

        # Migrate trades table before running schema (CHECK cannot be altered in place).
        # _migrate_trades_table is idempotent and handles the "table doesn't exist" case itself.
        _migrate_trades_table(conn)

        # Check for signals migration before running schema (table may already exist with old cols).
        existing_signals = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signals'"
        ).fetchone()
        if existing_signals:
            _migrate_signals_table(conn)

        conn.executescript(_SCHEMA_SQL)
        conn.commit()

        # Add strategy attribution columns to existing tables (D056).
        # Run after _SCHEMA_SQL so the tables are guaranteed to exist.
        _migrate_strategy_columns(conn)

        logger.info("Database initialized at %s", path)
    finally:
        conn.close()


@contextmanager
def get_connection(db_path: str | Path | None = None):
    """Context manager that yields a connection and closes it in a finally block."""
    path = _resolve_path(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


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
    strategy: str = "daily_lull",
) -> int:
    """Insert a new open trade and return its ID."""
    opened_at = _utc_now()
    cursor = conn.execute(
        """
        INSERT INTO trades (
            strategy, pair, direction, entry_price, stop_loss, take_profit,
            position_size, balance_at_open, opened_at, mt5_ticket
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strategy,
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
    logger.debug(
        "Logged new trade id=%s pair=%s direction=%s strategy=%s",
        trade_id,
        pair,
        direction,
        strategy,
    )
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
    rejection_reason: str | None = None,
    server_offset: timedelta = timedelta(0),
    strategy: str = "daily_lull",
) -> int:
    """Insert a signal record derived from a Signal dataclass. Returns signal ID.

    Pass rejection_reason when a risk check (not the strategy) caused the signal to be
    discarded — it overrides the decision to 'rejected' and replaces the reason string.

    The strategy computes candle/analysis timestamps in MT5 server time but tags them
    as UTC (+00:00) because MT5 returns epochs in server time (see D039).  Pass
    server_offset (e.g. GMT+3 → timedelta(hours=3)) so the three datetimes are converted
    to real UTC before persistence: subtracting the offset while keeping the +00:00 tag
    yields the correct UTC instant, matching trades.opened_at/closed_at which already use
    real UTC.  Defaults to zero offset (no conversion) for backward compatibility.

    Pass strategy to attribute the signal to a named strategy (default 'daily_lull').
    """
    if rejection_reason is not None:
        decision = "rejected"
        reason = rejection_reason
    else:
        decision = "accepted" if signal.action != "none" else "rejected"
        reason = signal.reason

    analyzed_at = (signal.timestamp - server_offset).isoformat()
    m15_candle_time = (signal.m15_candle_time - server_offset).isoformat()
    h4_candle_time = (signal.h4_candle_time - server_offset).isoformat()

    cursor = conn.execute(
        """
        INSERT INTO signals (
            strategy, pair, analyzed_at, m15_candle_time, h4_candle_time,
            range_high, range_low, range_atr_ratio,
            rsi, atr_value, h4_adx,
            decision, reason, trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strategy,
            signal.pair,
            analyzed_at,
            m15_candle_time,
            h4_candle_time,
            signal.range_high,
            signal.range_low,
            signal.range_atr_ratio,
            signal.rsi,
            signal.atr_value,
            signal.h4_adx,
            decision,
            reason,
            trade_id,
        ),
    )
    conn.commit()
    signal_id = cursor.lastrowid
    logger.debug(
        "Logged signal id=%s pair=%s decision=%s action=%s strategy=%s",
        signal_id,
        signal.pair,
        decision,
        signal.action,
        strategy,
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
    strategy: str | None = None,
) -> int:
    """Insert a bot event and return its ID.

    Pass strategy to tag the event as belonging to a specific strategy.
    strategy=None (default) means a global account-level event.
    """
    event_at = _utc_now()
    cursor = conn.execute(
        """
        INSERT INTO bot_events (event_at, event_type, strategy, detail, balance)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_at, event_type, strategy, detail, balance),
    )
    conn.commit()
    event_id = cursor.lastrowid
    logger.debug("Logged event id=%s type=%s strategy=%s", event_id, event_type, strategy)
    return event_id


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def log_peak_balance(
    conn: sqlite3.Connection,
    balance: float,
    strategy: str | None = None,
) -> None:
    """Record a new peak balance in bot_events.

    Pass strategy to tag it as a per-strategy peak; omit (default None) for the
    global account peak.
    """
    event_at = _utc_now()
    conn.execute(
        """
        INSERT INTO bot_events (event_at, event_type, strategy, balance)
        VALUES (?, 'peak_balance', ?, ?)
        """,
        (event_at, strategy, balance),
    )
    conn.commit()
    logger.debug("Logged peak_balance=%.2f strategy=%s", balance, strategy)


def get_stats(conn: sqlite3.Connection, strategy: str | None = None) -> dict:
    """Return aggregate statistics for recorded trades.

    If strategy is given, filters to only that strategy's trades.
    If strategy is None (default), aggregates across all strategies.
    """
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_trades,
            SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) AS winning_trades,
            SUM(CASE WHEN profit_loss <= 0 THEN 1 ELSE 0 END) AS losing_trades,
            SUM(profit_loss) AS total_pnl,
            MAX(balance_at_open) AS peak_balance_trades,
            AVG(duration_minutes) AS avg_trade_duration_minutes
        FROM trades
        WHERE closed_at IS NOT NULL AND (? IS NULL OR strategy = ?)
        """,
        (strategy, strategy),
    ).fetchone()

    # Also consider recorded peak_balance events (captures peaks between trades).
    # When a strategy is given, filter to its own events (mirrors the trades
    # query above); a NULL strategy aggregates across all (D062).
    event_row = conn.execute(
        "SELECT MAX(balance) AS peak_balance_events FROM bot_events"
        " WHERE event_type = 'peak_balance' AND (? IS NULL OR strategy = ?)",
        (strategy, strategy),
    ).fetchone()

    total = row["total_trades"] or 0
    winning = row["winning_trades"] or 0
    losing = row["losing_trades"] or 0
    win_rate = (winning / total) if total > 0 else 0.0

    peak_from_trades = row["peak_balance_trades"] or 0.0
    peak_from_events = (event_row["peak_balance_events"] or 0.0) if event_row else 0.0
    peak_balance = max(peak_from_trades, peak_from_events)

    return {
        "total_trades": total,
        "winning_trades": winning,
        "losing_trades": losing,
        "win_rate": win_rate,
        "total_pnl": row["total_pnl"] or 0.0,
        "peak_balance": peak_balance,
        "avg_trade_duration_minutes": row["avg_trade_duration_minutes"] or 0.0,
    }


# ---------------------------------------------------------------------------
# Strategy state CRUD (D056 / D053)
# ---------------------------------------------------------------------------


def get_strategy_state(conn: sqlite3.Connection, strategy: str) -> dict | None:
    """Return the strategy_state row for the given strategy, or None if absent."""
    row = conn.execute("SELECT * FROM strategy_state WHERE strategy = ?", (strategy,)).fetchone()
    return _row_to_dict(row) if row is not None else None


def upsert_strategy_peak(conn: sqlite3.Connection, strategy: str, peak_equity: float) -> None:
    """Insert or update the peak equity for a strategy.

    On conflict (strategy already exists), updates peak_equity and updated_at only
    if the new value is higher than the stored one.  The paused flag is never
    touched here — use set_strategy_paused for that.
    """
    updated_at = _utc_now()
    conn.execute(
        """
        INSERT INTO strategy_state (strategy, peak_equity, paused, updated_at)
        VALUES (?, ?, 0, ?)
        ON CONFLICT(strategy) DO UPDATE SET
            peak_equity = MAX(peak_equity, excluded.peak_equity),
            updated_at  = excluded.updated_at
        """,
        (strategy, peak_equity, updated_at),
    )
    conn.commit()
    logger.debug("Upserted peak_equity=%.2f for strategy=%s", peak_equity, strategy)


def seed_strategy_baseline(
    conn: sqlite3.Connection,
    strategy: str,
    baseline_capital: float,
    equity: float,
) -> None:
    """Seed the persisted notional baseline and reset the peak for a strategy (D062).

    Direct write (NOT MAX): unlike :func:`upsert_strategy_peak`, this sets
    ``peak_equity = equity`` unconditionally, overwriting any previous (inflated)
    peak so the per-strategy drawdown curve restarts clean from the deploy.  Called
    once when ``baseline_capital`` is still NULL.

    Args:
        conn: Open SQLite connection.
        strategy: Strategy name (primary key of strategy_state).
        baseline_capital: The notional baseline to persist (see
            :func:`drift.risk.seed_baseline_capital`).
        equity: The freshly computed equity to set as the new peak.
    """
    updated_at = _utc_now()
    conn.execute(
        """
        INSERT INTO strategy_state (strategy, peak_equity, paused, updated_at, baseline_capital)
        VALUES (?, ?, 0, ?, ?)
        ON CONFLICT(strategy) DO UPDATE SET
            peak_equity      = excluded.peak_equity,
            updated_at       = excluded.updated_at,
            baseline_capital = excluded.baseline_capital
        """,
        (strategy, equity, updated_at, baseline_capital),
    )
    conn.commit()
    logger.info(
        "Seeded baseline_capital=%.2f, reset peak_equity=%.2f for strategy=%s",
        baseline_capital,
        equity,
        strategy,
    )


def evaluate_strategy_drawdown(
    conn: sqlite3.Connection,
    strategy: str,
    allocation_pct: float,
    balance: float,
    floating: float,
    max_drawdown_percent: float,
) -> tuple[bool, str, float, float]:
    """Canonical per-strategy equity + drawdown brake (D062).

    The single implementation of the per-strategy equity curve, shared by the
    engine and the monitor (it replaces the divergent formulas that double-counted
    realized P&L — D062).  Equity is built on a persisted notional baseline, not
    the live balance:

        baseline_capital = balance * allocation_pct/100 - realized_lifetime_seed
        equity(t)        = baseline_capital + realized_lifetime(t) + floating(t)

    On the first call (``baseline_capital`` still NULL) the baseline is seeded once
    via :func:`drift.risk.seed_baseline_capital` and the existing (inflated) peak is
    reset to the freshly computed equity — the drawdown curve restarts clean from
    the deploy.  On every call the monotonic peak is updated via
    :func:`upsert_strategy_peak`.

    Args:
        conn: Open SQLite connection.
        strategy: Strategy name.
        allocation_pct: Strategy's notional allocation of the account (0-100).
        balance: Full account balance (MT5 balance, realized).
        floating: Unrealized P&L of the strategy's open positions.
        max_drawdown_percent: Per-strategy drawdown limit, in percent.

    Returns:
        ``(ok, reason, equity, peak)`` — ``ok`` is False once drawdown reaches the
        limit; ``reason`` is empty when ``ok`` is True.
    """
    realized_lifetime = get_stats(conn, strategy=strategy).get("total_pnl", 0.0) or 0.0

    row = get_strategy_state(conn, strategy)
    baseline = row["baseline_capital"] if row else None

    if baseline is None:
        # First call: seed the baseline once and restart the peak from the
        # freshly computed equity (overwriting any old inflated peak — D062).
        baseline = seed_baseline_capital(balance, allocation_pct, realized_lifetime)
        equity = strategy_equity(baseline, realized_lifetime, floating)
        seed_strategy_baseline(conn, strategy, baseline, equity)
        peak = equity
    else:
        # Already seeded: keep the monotonic high-water mark (upsert uses MAX).
        equity = strategy_equity(baseline, realized_lifetime, floating)
        peak = max((row["peak_equity"] or 0.0), equity)
        upsert_strategy_peak(conn, strategy, equity)

    ok, reason = check_strategy_drawdown(equity, peak, max_drawdown_percent)
    return ok, reason, equity, peak


def set_strategy_paused(conn: sqlite3.Connection, strategy: str, paused: bool) -> None:
    """Set the paused flag for a strategy.

    If no row exists yet, creates one with peak_equity=0.0 and the requested
    paused state.
    """
    updated_at = _utc_now()
    conn.execute(
        """
        INSERT INTO strategy_state (strategy, peak_equity, paused, updated_at)
        VALUES (?, 0.0, ?, ?)
        ON CONFLICT(strategy) DO UPDATE SET
            paused     = excluded.paused,
            updated_at = excluded.updated_at
        """,
        (strategy, int(paused), updated_at),
    )
    conn.commit()
    logger.debug("Set paused=%s for strategy=%s", paused, strategy)


def get_all_strategy_states(conn: sqlite3.Connection) -> list[dict]:
    """Return all rows from strategy_state ordered by strategy name."""
    rows = conn.execute("SELECT * FROM strategy_state ORDER BY strategy ASC").fetchall()
    return [_row_to_dict(r) for r in rows]
