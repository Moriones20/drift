"""Equivalence test: the unified backtest engine vs the legacy lull_engine (D055).

``backtest/engine.py`` drives the *live* ``DailyLullStrategy.on_bar`` over
historical bars; ``backtest/lull_engine.py`` is the legacy Backtesting.py port.
D055 requires the unified engine to reproduce the legacy results so the bot runs
live exactly what was validated.  These tests assert that equivalence on a fixed,
deterministic dataset.

Why the two CAN be made equivalent despite different code paths:

- The *signal logic* (entry conditions, SL value, TP=range-midpoint, the 02:00
  time stop) is identical because the unified engine literally calls the live
  ``on_bar``; the legacy engine is a faithful hand-port of the same rules.
- The *fill model* is replicated in ``backtest/engine.py`` to match
  Backtesting.py as ``lull_engine`` uses it: market entries and midpoint /
  time-stop exits fill at the next bar's open; the SL is an intrabar broker stop
  (checked on the entry bar too); commission is charged on both sides; sizing is
  all-in integer units.
- The only source of divergence found was indicator WARM-UP: the live ``on_bar``
  requests a bounded lookback (50 H4 bars), whose doubly-smoothed ADX has not
  converged, while ``lull_engine`` computes ADX over the full series.  The engine
  serves ``on_bar`` a deep-but-bounded warm-up window (``DEFAULT_WARMUP_BARS``)
  so the ADX converges to the same value — making the results bit-equivalent
  while keeping the loop linear.  This is documented as D059.

The dataset is the seeded synthetic generator (``generate_synthetic_m15``)
sliced to ~80 days, which deterministically produces buy and sell entries that
exit via take-profit, stop-loss, and the 02:00 time stop — all three paths.
A second test runs against the real CSVs in ``backtest/data/`` when present.
"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.download_data import generate_synthetic_m15, load_lull_data  # noqa: E402
from backtest.engine import build_lull_strategy, run_backtest  # noqa: E402
from backtest.lull_engine import prepare_lull_data, run_lull_backtest  # noqa: E402

# lull_engine logs a line per evaluated bar; silence it for the test.
logging.disable(logging.CRITICAL)

CASH = 500.0
COMMISSION = 0.00007
DATA_DIR = Path(__file__).parent.parent / "backtest" / "data"


def _synthetic_slice(symbol: str, days: int = 80):
    """Return a deterministic (H4, M15) slice spanning ``days`` trading days."""
    h4, m15 = generate_synthetic_m15(symbol, years=1)
    m15s = m15.iloc[: days * 96]  # 96 M15 bars per day
    h4s = h4.loc[h4.index <= m15s.index[-1]]
    return h4s, m15s


def _lull_trades(stats) -> list[tuple]:
    """Normalise lull_engine trades to (entry, exit, direction) tuples + pnls."""
    df = stats["_trades"]
    rows = []
    pnls = []
    for t in df.itertuples(index=False):
        direction = "buy" if t.Size > 0 else "sell"
        rows.append((str(t.EntryTime), str(t.ExitTime), direction))
        pnls.append(float(t.PnL))
    return rows, pnls


def _engine_trades(result) -> list[tuple]:
    """Normalise engine trades to (entry, exit, direction) tuples + pnls."""
    rows = []
    pnls = []
    for t in result.trades:
        rows.append((str(t.entry_time), str(t.exit_time), t.direction))
        pnls.append(t.pnl)
    return rows, pnls


def _assert_equivalent(stats, result, *, pnl_tol=1e-4, metric_rel_tol=1e-3):
    """Assert lull_engine ``stats`` and engine ``result`` are equivalent."""
    lull_rows, lull_pnls = _lull_trades(stats)
    eng_rows, eng_pnls = _engine_trades(result)

    # 1. Same trades: same count, and the same (entry, exit, direction) set.
    assert len(eng_rows) == len(lull_rows), (
        f"trade count differs: engine={len(eng_rows)} lull={len(lull_rows)}"
    )
    assert sorted(eng_rows) == sorted(lull_rows), "trade (entry, exit, direction) lists differ"

    # 2. Per-trade P&L matches within a tiny absolute tolerance (sub-cent).
    #    Pair trades by their (entry, exit, direction) key, then compare P&L.
    lull_sorted = sorted(zip(lull_rows, lull_pnls))
    eng_sorted = sorted(zip(eng_rows, eng_pnls))
    for (lkey, lpnl), (ekey, epnl) in zip(lull_sorted, eng_sorted):
        assert lkey == ekey, f"trade key mismatch: {lkey} vs {ekey}"
        assert abs(lpnl - epnl) <= pnl_tol, f"per-trade P&L differs at {lkey}: {lpnl} vs {epnl}"

    # 3. Aggregate metrics match within a small relative tolerance.
    m = result.metrics
    checks = {
        "return_pct": float(stats["Return [%]"]),
        "win_rate": float(stats["Win Rate [%]"]),
        "max_drawdown_pct": float(stats["Max. Drawdown [%]"]),
        "sharpe": float(stats["Sharpe Ratio"]),
    }
    for key, lull_val in checks.items():
        eng_val = m[key]
        assert eng_val == pytest.approx(lull_val, rel=metric_rel_tol, abs=1e-6), (
            f"{key}: engine={eng_val} lull={lull_val}"
        )

    # Profit factor can be inf/nan when there are no losers/winners; guard it.
    pf_lull = float(stats["Profit Factor"])
    pf_eng = m["profit_factor"]
    if pf_lull == pf_lull and pf_lull not in (float("inf"),):  # finite, not NaN
        assert pf_eng == pytest.approx(pf_lull, rel=metric_rel_tol), (
            f"profit_factor: engine={pf_eng} lull={pf_lull}"
        )

    assert m["trades"] == int(stats["# Trades"])


def test_equivalence_on_synthetic_dataset():
    """The unified engine reproduces lull_engine on a deterministic synthetic slice."""
    symbol = "GBPJPY"
    h4, m15 = _synthetic_slice(symbol)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = prepare_lull_data(h4, m15)
        stats, _bt = run_lull_backtest(df, cash=CASH, commission=COMMISSION)

    strategy = build_lull_strategy([symbol])
    result = run_backtest(strategy, m15, h4, symbol, cash=CASH, commission=COMMISSION)

    # The fixture is chosen so all three exit paths occur.
    reasons = {t.reason for t in result.trades}
    assert {"tp", "sl", "time_stop"} <= reasons, f"fixture should exercise all exits, got {reasons}"

    _assert_equivalent(stats, result)


def test_engine_trade_record_fields():
    """Engine trades carry coherent direction/price/reason fields."""
    symbol = "GBPJPY"
    h4, m15 = _synthetic_slice(symbol)
    result = run_backtest(build_lull_strategy([symbol]), m15, h4, symbol, cash=CASH)

    assert result.trades, "expected at least one trade on the fixture"
    for t in result.trades:
        assert t.direction in ("buy", "sell")
        assert t.reason in ("tp", "sl", "time_stop")
        assert t.exit_time >= t.entry_time
        assert t.entry_price > 0 and t.exit_price > 0
        # A long is sized with positive units, a short with negative.
        assert (t.size > 0) == (t.direction == "buy")


@pytest.mark.parametrize("symbol", ["EURCHF", "GBPJPY"])
def test_equivalence_on_real_data_when_available(symbol):
    """When the real CSVs exist, the engine reproduces lull_engine there too.

    Full-history runs drive ``on_bar`` over tens of thousands of bars, so this is
    slow (tens of seconds per pair).  It is opt-in: set the environment variable
    ``DRIFT_RUN_SLOW_BACKTEST=1`` to run it.  The synthetic-slice test above is
    the always-on equivalence guard.
    """
    if not os.environ.get("DRIFT_RUN_SLOW_BACKTEST"):
        pytest.skip("set DRIFT_RUN_SLOW_BACKTEST=1 to run the slow real-data equivalence test")
    if not (DATA_DIR / f"{symbol}_M15.csv").exists():
        pytest.skip(f"real data for {symbol} not present in {DATA_DIR}")

    h4, m15 = load_lull_data(symbol, DATA_DIR)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = prepare_lull_data(h4, m15)
        stats, _bt = run_lull_backtest(df, cash=CASH, commission=COMMISSION)

    strategy = build_lull_strategy([symbol])
    result = run_backtest(strategy, m15, h4, symbol, cash=CASH, commission=COMMISSION)

    _assert_equivalent(stats, result)


# ===========================================================================
# London ORB engine tests (broker_tp exit model)
# ===========================================================================
#
# These tests drive the live ``LondonOrbStrategy.on_bar`` over a small,
# deterministic synthetic M15 series and assert the exact fill prices the
# engine produces under the ``broker_tp`` exit model: a real broker limit TP
# filled intrabar (symmetric to the SL), with the SL winning when both are hit
# in the same bar.  They exercise a BUY and a SELL, an SL fill, a broker-TP
# fill (including a gap-through fill BETTER than the TP), the SL+TP same-bar
# precedence (SL wins), and the 18:00 time stop.

import pandas as pd  # noqa: E402

from drift.strategies.london_orb import LondonOrbParams, LondonOrbStrategy  # noqa: E402

ORB_PAIR = "EURUSD"  # pip_multiplier 10000, base price ~1.10
# Filler-bar geometry: every bar is a 10-pip-range doji at the base price, so
# the M15 ATR(14) converges to 0.0010 immediately and no filler bar ever breaks
# a range (high/low stay inside any locked range we build).
_BASE = 1.10000
_FILLER_HALF = 0.00050  # half of the 10-pip filler range
_M15 = pd.Timedelta(minutes=15)


def _orb_strategy() -> LondonOrbStrategy:
    """An ORB strategy with default params and no pip floor (floor 0)."""
    return LondonOrbStrategy([ORB_PAIR], LondonOrbParams())


def _filler_bar() -> dict:
    """A benign 10-pip doji centred on the base price (never breaks a range)."""
    return {
        "open": _BASE,
        "high": _BASE + _FILLER_HALF,
        "low": _BASE - _FILLER_HALF,
        "close": _BASE,
    }


def _build_m15(day_overrides: dict[pd.Timestamp, dict], n_days: int = 3) -> pd.DataFrame:
    """Build ``n_days`` of 96 M15 bars/day, applying per-timestamp OHLC overrides.

    The index is a tz-aware (UTC-labelled, reasoned over as server time)
    DatetimeIndex.  Day 0 starts at 2024-01-01 (a Monday); every slot defaults to
    a filler doji and is replaced by ``day_overrides[timestamp]`` when present.
    """
    start = pd.Timestamp("2024-01-01 00:00", tz="UTC")  # Monday
    rows = []
    idx = []
    for d in range(n_days):
        for slot in range(96):
            ts = start + pd.Timedelta(days=d) + slot * _M15
            bar = day_overrides.get(ts, _filler_bar())
            rows.append(bar)
            idx.append(ts)
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
    df["volume"] = 1.0
    return df


def _empty_h4() -> pd.DataFrame:
    """An empty H4 frame: the ORB never reads H4, so it is never accessed."""
    return pd.DataFrame(
        {"open": [], "high": [], "low": [], "close": [], "volume": []},
        index=pd.DatetimeIndex([], tz="UTC"),
    )


def _range_bars(day: int) -> dict[pd.Timestamp, dict]:
    """Range-definition bars for 10:00-10:45 of ``day``: high 1.1015, low 1.1000.

    Width 15 pips; ATR is 0.0010, so the ratio is 1.5 (inside the default
    [0.5, 2.0] window) and the range locks tradeable.  range_high=1.10150,
    range_low=1.10000.
    """
    base_day = pd.Timestamp("2024-01-01 00:00", tz="UTC") + pd.Timedelta(days=day)
    h10 = base_day + pd.Timedelta(hours=10)
    out: dict[pd.Timestamp, dict] = {}
    # Four 10:xx bars whose combined high/low define the range.
    out[h10] = {"open": 1.10050, "high": 1.10150, "low": 1.10050, "close": 1.10100}
    out[h10 + _M15] = {"open": 1.10100, "high": 1.10120, "low": 1.10000, "close": 1.10050}
    out[h10 + 2 * _M15] = {"open": 1.10050, "high": 1.10100, "low": 1.10030, "close": 1.10080}
    out[h10 + 3 * _M15] = {"open": 1.10080, "high": 1.10130, "low": 1.10060, "close": 1.10100}
    return out


def _run_orb(day_overrides: dict[pd.Timestamp, dict]) -> object:
    """Run the ORB backtest over a 3-day synthetic series with the given bars."""
    m15 = _build_m15(day_overrides)
    h4 = _empty_h4()
    return run_backtest(_orb_strategy(), m15, h4, ORB_PAIR, cash=CASH, commission=COMMISSION)


def _orb_setup(day: int = 1) -> tuple[dict[pd.Timestamp, dict], pd.Timestamp]:
    """Common ORB scenario start: the locked range plus an inside-range 11:00 bar.

    Returns the per-timestamp overrides (range bars 10:00-10:45 + a non-breakout
    11:00 lock candle) and the 11:00 timestamp, from which each test adds only the
    bars that differ.  range_high=1.10150, range_low=1.10000, width 0.00150.
    """
    base_day = pd.Timestamp("2024-01-01 00:00", tz="UTC") + pd.Timedelta(days=day)
    h11 = base_day + pd.Timedelta(hours=11)
    overrides = _range_bars(day)
    # 11:00 lock candle stays inside the range (no breakout here).
    overrides[h11] = {"open": 1.10100, "high": 1.10140, "low": 1.10090, "close": 1.10120}
    return overrides, h11


def test_orb_buy_broker_tp_fill():
    """A BUY breakout exits at the broker TP, filled intrabar at the limit."""
    overrides, h11 = _orb_setup()
    # 11:15 breakout: close 1.10200 > range_high 1.10150 -> BUY queued.
    overrides[h11 + _M15] = {"open": 1.10160, "high": 1.10210, "low": 1.10150, "close": 1.10200}
    # 11:30 entry bar: fills at this open (1.10180); stays below TP and above SL.
    overrides[h11 + 2 * _M15] = {"open": 1.10180, "high": 1.10220, "low": 1.10170, "close": 1.10200}
    # 11:45 hits the TP (1.10300) intrabar; open below TP so fill is exactly TP.
    overrides[h11 + 3 * _M15] = {"open": 1.10250, "high": 1.10320, "low": 1.10240, "close": 1.10300}

    result = _run_orb(overrides)
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.direction == "buy"
    assert t.reason == "tp"
    # Entry fills at the 11:30 open.
    assert t.entry_price == pytest.approx(1.10180)
    # TP = range_high + 1.0 * width = 1.10150 + 0.00150 = 1.10300; open below it,
    # so the limit fills exactly at the TP.
    assert t.exit_price == pytest.approx(1.10300)
    assert t.exit_time == (h11 + 3 * _M15).to_pydatetime()


def test_orb_buy_broker_tp_gap_fill_is_better_than_tp():
    """A gap-up through the TP fills BETTER than the limit (at the bar open)."""
    overrides, h11 = _orb_setup()
    overrides[h11 + _M15] = {"open": 1.10160, "high": 1.10210, "low": 1.10150, "close": 1.10200}
    # 11:30 entry at open 1.10180, no exit this bar.
    overrides[h11 + 2 * _M15] = {"open": 1.10180, "high": 1.10220, "low": 1.10170, "close": 1.10200}
    # 11:45 gaps UP straight past the TP (1.10300): open 1.10350 > tp -> fill at
    # the better price max(open, tp) = 1.10350.
    overrides[h11 + 3 * _M15] = {"open": 1.10350, "high": 1.10400, "low": 1.10340, "close": 1.10380}

    result = _run_orb(overrides)
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.direction == "buy"
    assert t.reason == "tp"
    assert t.exit_price == pytest.approx(1.10350)  # better than the 1.10300 TP


def test_orb_buy_stop_loss_fill():
    """A BUY that reverses below the range low exits at the SL, intrabar."""
    overrides, h11 = _orb_setup()
    overrides[h11 + _M15] = {"open": 1.10160, "high": 1.10210, "low": 1.10150, "close": 1.10200}
    # 11:30 entry at open 1.10180.
    overrides[h11 + 2 * _M15] = {"open": 1.10180, "high": 1.10220, "low": 1.10170, "close": 1.10200}
    # 11:45 drops through the SL (range_low 1.10000): low 1.09950 <= SL, open
    # above SL so fill is exactly the SL (1.10000).
    overrides[h11 + 3 * _M15] = {"open": 1.10100, "high": 1.10110, "low": 1.09950, "close": 1.10000}

    result = _run_orb(overrides)
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.direction == "buy"
    assert t.reason == "sl"
    assert t.exit_price == pytest.approx(1.10000)  # SL = range_low


def test_orb_sell_broker_tp_fill():
    """A SELL breakout below the range low exits at the broker TP, intrabar."""
    overrides, h11 = _orb_setup()
    # 11:15 breakout DOWN: close 1.09950 < range_low 1.10000 -> SELL queued.
    overrides[h11 + _M15] = {"open": 1.10000, "high": 1.10010, "low": 1.09940, "close": 1.09950}
    # 11:30 entry at open 1.09980.
    overrides[h11 + 2 * _M15] = {"open": 1.09980, "high": 1.09990, "low": 1.09900, "close": 1.09920}
    # 11:45 hits the SELL TP (range_low - width = 1.10000 - 0.00150 = 1.09850);
    # low 1.09800 <= TP, open above TP so fill is exactly the TP.
    overrides[h11 + 3 * _M15] = {"open": 1.09900, "high": 1.09910, "low": 1.09800, "close": 1.09850}

    result = _run_orb(overrides)
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.direction == "sell"
    assert t.reason == "tp"
    assert t.entry_price == pytest.approx(1.09980)
    assert t.exit_price == pytest.approx(1.09850)  # TP = range_low - width


def test_orb_sl_and_tp_same_bar_sl_wins():
    """When SL and TP are both inside one bar, the SL fills first (conservative)."""
    overrides, h11 = _orb_setup()
    overrides[h11 + _M15] = {"open": 1.10160, "high": 1.10210, "low": 1.10150, "close": 1.10200}
    # 11:30 entry bar at open 1.10180 — and this SAME bar straddles BOTH the TP
    # (1.10300, high reaches it) and the SL (1.10000, low reaches it).  The SL
    # must win: exit reason "sl" at 1.10000, not "tp" at 1.10300.
    overrides[h11 + 2 * _M15] = {"open": 1.10180, "high": 1.10320, "low": 1.09950, "close": 1.10100}

    result = _run_orb(overrides)
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.direction == "buy"
    assert t.reason == "sl"
    assert t.exit_price == pytest.approx(1.10000)
    assert t.exit_time == (h11 + 2 * _M15).to_pydatetime()


def test_orb_time_stop_at_1800():
    """An open position with no SL/TP touch is force-closed at the 18:00 stop."""
    overrides, h11 = _orb_setup()
    base_day = pd.Timestamp("2024-01-01 00:00", tz="UTC") + pd.Timedelta(days=1)
    # 11:15 breakout BUY, entry at the 11:30 open.
    overrides[h11 + _M15] = {"open": 1.10160, "high": 1.10210, "low": 1.10150, "close": 1.10200}
    overrides[h11 + 2 * _M15] = {"open": 1.10180, "high": 1.10220, "low": 1.10170, "close": 1.10200}
    # All bars from 11:45 until 18:00 stay strictly between SL (1.10000) and TP
    # (1.10300): the filler doji (1.0995-1.1005) would dip below the SL, so hold
    # the price flat at 1.10200 across the rest of the session.
    held = {"open": 1.10200, "high": 1.10220, "low": 1.10180, "close": 1.10200}
    t = h11 + 3 * _M15
    stop_bar = base_day + pd.Timedelta(hours=18)
    # Cover through 18:15 too: the time-stop close fills at the 18:15 open.
    while t <= stop_bar + _M15:
        overrides[t] = dict(held)
        t += _M15

    result = _run_orb(overrides)
    assert len(result.trades) == 1
    tr = result.trades[0]
    assert tr.direction == "buy"
    assert tr.reason == "time_stop"
    # close_all at 18:00 fills at the NEXT bar's open (18:15 open = 1.10200).
    assert tr.exit_price == pytest.approx(1.10200)
    assert tr.exit_time == (stop_bar + _M15).to_pydatetime()


def test_orb_uses_broker_tp_exit_model():
    """The ORB strategy declares the broker-TP exit model the engine selects on."""
    assert getattr(LondonOrbStrategy, "backtest_exit_model", None) == "broker_tp"
