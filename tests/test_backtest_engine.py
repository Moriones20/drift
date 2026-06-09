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
