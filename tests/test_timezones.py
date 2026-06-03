"""Tests for the D039 timezone fix.

Covers the two correctness boundaries introduced by D039:

1. Persistence: log_signal() converts strategy timestamps from MT5 server time
   (tagged as UTC) to real UTC by subtracting the server offset.
2. Display: parse_utc_offset() + format_time() render a real-UTC datetime in the
   configured display timezone (Bogotá / UTC-5).
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure project root is on the path when run directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from drift.db import init_db, log_signal  # noqa: E402
from drift.formatting import format_time, parse_utc_offset  # noqa: E402
from drift.strategy import Signal  # noqa: E402


def _make_test_db() -> tuple[str, sqlite3.Connection]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return db_path, conn


def _make_signal(ts: datetime) -> Signal:
    return Signal(
        pair="EURJPY",
        timestamp=ts,
        m15_candle_time=ts,
        h4_candle_time=ts,
        entry_price=160.00,
        sl=159.50,
        tp=161.00,
        range_high=160.50,
        range_low=159.50,
        range_atr_ratio=2.0,
        rsi=28.0,
        atr_value=0.05,
        h4_adx=15.0,
        action="buy",
        reason="lull_scalper_buy",
    )


class TestLogSignalServerOffset(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path, self.conn = _make_test_db()

    def tearDown(self) -> None:
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def test_m15_candle_time_persisted_in_real_utc(self) -> None:
        # Candle close stamped 2026-06-02 23:00 in MT5 server time but tagged
        # as +00:00 (how MT5 epochs arrive).  Real UTC is 3h earlier.
        server_ts = datetime(2026, 6, 2, 23, 0, tzinfo=timezone.utc)
        signal = _make_signal(server_ts)

        signal_id = log_signal(self.conn, signal, trade_id=None, server_offset=timedelta(hours=3))

        row = self.conn.execute(
            "SELECT m15_candle_time, analyzed_at, h4_candle_time FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()

        stored = datetime.fromisoformat(row["m15_candle_time"])
        expected = datetime(2026, 6, 2, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(stored, expected)
        # All three timestamps share the same conversion.
        self.assertEqual(datetime.fromisoformat(row["analyzed_at"]), expected)
        self.assertEqual(datetime.fromisoformat(row["h4_candle_time"]), expected)

    def test_zero_offset_leaves_timestamp_unchanged(self) -> None:
        ts = datetime(2026, 6, 2, 23, 0, tzinfo=timezone.utc)
        signal_id = log_signal(self.conn, _make_signal(ts), trade_id=None)
        row = self.conn.execute(
            "SELECT m15_candle_time FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()
        self.assertEqual(datetime.fromisoformat(row["m15_candle_time"]), ts)


class TestParseAndFormat(unittest.TestCase):
    def test_parse_utc_offset_variants(self) -> None:
        self.assertEqual(parse_utc_offset("UTC-5"), timezone(timedelta(hours=-5)))
        self.assertEqual(parse_utc_offset("UTC+3"), timezone(timedelta(hours=3)))
        self.assertEqual(parse_utc_offset("UTC"), timezone(timedelta(0)))
        self.assertEqual(parse_utc_offset(" UTC -5 "), timezone(timedelta(hours=-5)))

    def test_parse_utc_offset_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_utc_offset("America/Bogota")

    def test_format_time_renders_bogota_from_real_utc(self) -> None:
        # Real-UTC instant 2026-06-02 20:00 → Bogotá (UTC-5) is 15:00 same day.
        real_utc = datetime(2026, 6, 2, 20, 0, tzinfo=timezone.utc)
        bogota = parse_utc_offset("UTC-5")
        self.assertEqual(format_time(real_utc, tz=bogota), "2026-06-02 15:00")


if __name__ == "__main__":
    unittest.main()
