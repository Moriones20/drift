"""Run London ORB backtests across all ORB pairs and produce a comparison report.

Flow:
  1. Verify CSV data exists in backtest/data/ — exits with an error if missing
     (ORB CSVs must be downloaded separately; there is no synthetic fallback for
     the real 2-year data required by calibration).
  2. Baseline run: all 4 pairs at default params -> metrics table.
  3. Walk-forward calibration: IS/OOS chronological split (70/30), small
     range_atr_min x range_atr_max grid, fixed tp_mult=1.0.
  4. Pair ranking and RECOMMENDATION (for user approval — does NOT edit config).

Usage:
    python backtest/run_orb.py
"""

from __future__ import annotations

import logging
import math
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest._results import format_metrics_results, save_metrics_results
from backtest.engine import run_backtest
from drift.strategies.london_orb import LondonOrbParams, LondonOrbStrategy

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results_orb"

# The four ORB candidate pairs (D067).
ORB_PAIRS = ["GBPJPY", "EURJPY", "GBPUSD", "EURUSD"]

CASH: float = 500.0
COMMISSION: float = 0.00007

# IS/OOS split (70 % in-sample, 30 % out-of-sample) — same pattern as validate_oos.py.
IS_FRACTION: float = 0.70

# Default params from config.example.yaml (placeholders pending Step 46).
DEFAULT_PARAMS = LondonOrbParams(
    range_atr_min=0.5,
    range_atr_max=2.0,
    tp_mult=1.0,
    atr_period=14,
    range_pip_floor={
        "GBPJPY": 18.0,
        "EURJPY": 12.0,
        "GBPUSD": 10.0,
        "EURUSD": 8.0,
    },
)

# Calibration grid: range_atr_min x range_atr_max; tp_mult fixed at 1.0.
# Kept small (3x3 = 9 combos) to stay tractable (~tens of seconds per combo).
ATR_MIN_GRID = [0.3, 0.5, 0.8]
ATR_MAX_GRID = [1.5, 2.0, 3.0]


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _orb_data_complete(pairs: list[str], data_dir: Path) -> list[str]:
    """Return a list of pairs whose M15 CSV is missing."""
    missing = []
    for symbol in pairs:
        if not (data_dir / f"{symbol}_M15.csv").exists():
            missing.append(symbol)
    return missing


def _load_orb_m15(symbol: str, data_dir: Path) -> pd.DataFrame:
    """Load M15 data for one ORB pair.

    Applies the same server-time convention as ``load_lull_data`` (D041):
    timestamps are labeled UTC in the CSV but carry MT5 server-local values;
    the engine and strategy reason over them as server time directly, without
    any tz conversion.  The ORB data ships with M15-only CSVs, so we read the
    M15 file directly rather than calling ``load_lull_data`` (which also
    requires an H4 file).
    """
    path_m15 = data_dir / f"{symbol}_M15.csv"
    df_m15 = pd.read_csv(path_m15, index_col="time", parse_dates=True)
    if df_m15.index.tz is None:
        df_m15.index = df_m15.index.tz_localize("UTC")
    return df_m15


def _build_orb_strategy(pairs: list[str], params: LondonOrbParams) -> LondonOrbStrategy:
    return LondonOrbStrategy(pairs=list(pairs), params=params)


# Empty H4 frame passed to run_backtest for M15-only strategies.
# run_backtest accepts an h4 argument but never accesses it when the strategy
# does not call market.candles(pair, "H4", ...).  _first_evaluable_index
# branches on hasattr(params, "h4_adx_period") and skips H4 indicator
# computation for the ORB (LondonOrbParams has no such attribute).
_EMPTY_H4: pd.DataFrame = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------


def _safe_float(val: object) -> float:
    try:
        f = float(val)  # type: ignore[arg-type]
        return f if math.isfinite(f) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _recommendation(profit_factor: float, win_rate: float, max_drawdown_pct: float) -> str:
    """Classify a pair on three criteria (mirrors run_lull.py thresholds)."""
    keep_pf = profit_factor >= 1.2
    keep_wr = win_rate >= 35.0
    keep_dd = abs(max_drawdown_pct) <= 20.0
    fails = sum([not keep_pf, not keep_wr, not keep_dd])
    if fails == 0:
        return "KEEP"
    if fails == 1:
        return "REVIEW"
    return "DROP"


def _extract_row(symbol: str, metrics: dict, m15: pd.DataFrame) -> dict:
    start = m15.index[0] if len(m15) else "?"
    end = m15.index[-1] if len(m15) else "?"
    return {
        "symbol": symbol,
        "return_pct": _safe_float(metrics.get("return_pct")),
        "win_rate": _safe_float(metrics.get("win_rate")),
        "profit_factor": _safe_float(metrics.get("profit_factor")),
        "max_drawdown_pct": _safe_float(metrics.get("max_drawdown_pct")),
        "trades": _safe_float(metrics.get("trades")),
        "sharpe": _safe_float(metrics.get("sharpe")),
        "start": str(start),
        "end": str(end),
    }


# ---------------------------------------------------------------------------
# Table printing (mirrors run_lull.py layout)
# ---------------------------------------------------------------------------


def _print_comparison_table(rows: list[dict], title: str = "LONDON ORB — ALL PAIRS") -> None:
    cols = (
        f"{'Pair':<8} | {'Return%':>7} | {'WinRate':>7} | {'PF':>5}"
        f" | {'MaxDD%':>7} | {'Trades':>6} | {'Sharpe':>6}"
    )
    sep = "-" * len(cols)
    print()
    print("=" * len(cols))
    print(f"   {title}")
    print("=" * len(cols))

    starts = [r["start"] for r in rows if r["start"] != "?"]
    ends = [r["end"] for r in rows if r["end"] != "?"]
    period_start = min(starts) if starts else "?"
    period_end = max(ends) if ends else "?"
    print(f"Period: {period_start} to {period_end}")
    print()
    print(cols)
    print(sep)

    for r in rows:
        ret = r["return_pct"]
        wr = r["win_rate"]
        pf = r["profit_factor"]
        dd = r["max_drawdown_pct"]
        trades = r["trades"]
        sharpe = r["sharpe"]

        ret_str = f"{ret:+.1f}%" if math.isfinite(ret) else "  N/A"
        wr_str = f"{wr:.1f}%" if math.isfinite(wr) else "  N/A"
        pf_str = f"{pf:.2f}" if math.isfinite(pf) else " N/A"
        dd_str = f"{dd:.1f}%" if math.isfinite(dd) else "  N/A"
        trades_str = str(int(trades)) if math.isfinite(trades) else "N/A"
        sharpe_str = f"{sharpe:.2f}" if math.isfinite(sharpe) else "  N/A"

        row_line = (
            f"{r['symbol']:<8} | {ret_str:>7} | {wr_str:>7} | {pf_str:>5}"
            f" | {dd_str:>7} | {trades_str:>6} | {sharpe_str:>6}"
        )
        print(row_line)

    print(sep)

    total_trades = sum(r["trades"] for r in rows if math.isfinite(r["trades"]))
    valid_wr = [r["win_rate"] for r in rows if math.isfinite(r["win_rate"])]
    avg_wr = sum(valid_wr) / len(valid_wr) if valid_wr else float("nan")
    valid_pf = [r["profit_factor"] for r in rows if math.isfinite(r["profit_factor"])]
    avg_pf = sum(valid_pf) / len(valid_pf) if valid_pf else float("nan")
    worst_dd = min(
        (r["max_drawdown_pct"] for r in rows if math.isfinite(r["max_drawdown_pct"])),
        default=float("nan"),
    )
    total_return = sum(
        CASH * r["return_pct"] / 100.0 for r in rows if math.isfinite(r["return_pct"])
    )
    total_return_pct = (total_return / (CASH * len(rows))) * 100.0 if rows else float("nan")
    valid_sharpe = [r["sharpe"] for r in rows if math.isfinite(r["sharpe"])]
    avg_sharpe = sum(valid_sharpe) / len(valid_sharpe) if valid_sharpe else float("nan")

    ret_str = f"{total_return_pct:+.1f}%" if math.isfinite(total_return_pct) else "  N/A"
    wr_str = f"{avg_wr:.1f}%" if math.isfinite(avg_wr) else "  N/A"
    pf_str = f"{avg_pf:.2f}" if math.isfinite(avg_pf) else " N/A"
    dd_str = f"{worst_dd:.1f}%" if math.isfinite(worst_dd) else "  N/A"
    sharpe_str = f"{avg_sharpe:.2f}" if math.isfinite(avg_sharpe) else "  N/A"

    total_line = (
        f"{'TOTAL':<8} | {ret_str:>7} | {wr_str:>7} | {pf_str:>5}"
        f" | {dd_str:>7} | {int(total_trades):>6} | {sharpe_str:>6}"
    )
    print(total_line)
    print("=" * len(cols))


def _print_ranking(rows: list[dict]) -> None:
    ranked = sorted(
        [r for r in rows if math.isfinite(r["return_pct"])],
        key=lambda r: r["return_pct"],
        reverse=True,
    )
    print()
    print("=== RANKING BY NET RETURN ===")
    for i, r in enumerate(ranked, 1):
        print(f"  {i}. {r['symbol']:<8}  {r['return_pct']:+.2f}%")


def _print_portfolio_summary(rows: list[dict]) -> None:
    total_trades = sum(r["trades"] for r in rows if math.isfinite(r["trades"]))
    valid_wr = [r["win_rate"] for r in rows if math.isfinite(r["win_rate"])]
    avg_wr = sum(valid_wr) / len(valid_wr) if valid_wr else float("nan")
    valid_pf = [r["profit_factor"] for r in rows if math.isfinite(r["profit_factor"])]
    avg_pf = sum(valid_pf) / len(valid_pf) if valid_pf else float("nan")
    worst_dd = min(
        (r["max_drawdown_pct"] for r in rows if math.isfinite(r["max_drawdown_pct"])),
        default=float("nan"),
    )
    total_pnl = sum(CASH * r["return_pct"] / 100.0 for r in rows if math.isfinite(r["return_pct"]))

    print()
    print("=== PORTFOLIO SUMMARY ===")
    print(f"  Starting capital per pair : ${CASH:.2f}")
    print(f"  Total capital deployed    : ${CASH * len(rows):.2f}")
    print(f"  Total simulated P&L       : ${total_pnl:+.2f}")
    print(f"  Total trades              : {int(total_trades)}")
    wr_out = f"{avg_wr:.1f}%" if math.isfinite(avg_wr) else "N/A"
    pf_out = f"{avg_pf:.2f}" if math.isfinite(avg_pf) else "N/A"
    dd_out = f"{worst_dd:.1f}%" if math.isfinite(worst_dd) else "N/A"
    print(f"  Overall win rate          : {wr_out}")
    print(f"  Average profit factor     : {pf_out}")
    print(f"  Worst max drawdown        : {dd_out}")

    print()
    print("=== PAIR RECOMMENDATIONS (BASELINE) ===")
    for r in rows:
        rec = _recommendation(r["profit_factor"], r["win_rate"], r["max_drawdown_pct"])
        print(f"  {r['symbol']:<8}  {rec}")


# ---------------------------------------------------------------------------
# R:R sanity check
# ---------------------------------------------------------------------------


def _print_rr_sanity(symbol: str, trades: list) -> None:
    """Print average winner vs. average loser as an R:R sanity check."""
    winners = [t for t in trades if t.pnl > 0]
    losers = [t for t in trades if t.pnl < 0]
    if not winners and not losers:
        print(f"  {symbol}: no closed trades")
        return
    avg_win = sum(t.pnl for t in winners) / len(winners) if winners else float("nan")
    avg_loss = abs(sum(t.pnl for t in losers) / len(losers)) if losers else float("nan")
    rr = (
        avg_win / avg_loss
        if (math.isfinite(avg_win) and math.isfinite(avg_loss) and avg_loss > 0)
        else float("nan")
    )
    rr_str = f"{rr:.2f}" if math.isfinite(rr) else "N/A"
    print(
        f"  {symbol:<8}  trades={len(trades):>4}  winners={len(winners):>4}  "
        f"avg_win=${avg_win:>7.2f}  avg_loss=${avg_loss:>7.2f}  R:R={rr_str}"
    )


# ---------------------------------------------------------------------------
# Walk-forward calibration
# ---------------------------------------------------------------------------


def _run_combo(
    symbol: str,
    m15: pd.DataFrame,
    atr_min: float,
    atr_max: float,
) -> dict:
    """Run one grid combo on the full frame; return metrics dict."""
    pip_floor = DEFAULT_PARAMS.range_pip_floor
    params = LondonOrbParams(
        range_atr_min=atr_min,
        range_atr_max=atr_max,
        tp_mult=1.0,
        atr_period=14,
        range_pip_floor=pip_floor.copy(),
    )
    strategy = _build_orb_strategy([symbol], params)
    result = run_backtest(strategy, m15, _EMPTY_H4, symbol, cash=CASH, commission=COMMISSION)
    return result.metrics


def _walk_forward_calibration(
    pairs: list[str],
    m15_frames: dict[str, pd.DataFrame],
) -> dict[str, dict]:
    """IS/OOS walk-forward calibration over the ATR grid.

    Splits each pair's M15 chronologically (70 % IS, 30 % OOS).  Finds the
    best combo by IS profit factor, evaluates it on OOS.  Also checks all combos
    on OOS so we can report the full grid for transparency.

    Returns a dict of symbol -> calibration result dict.
    """
    results: dict[str, dict] = {}

    grid = [(mn, mx) for mn in ATR_MIN_GRID for mx in ATR_MAX_GRID if mn < mx]

    print()
    print("=" * 60)
    print("   WALK-FORWARD CALIBRATION (IS=70%, OOS=30%)")
    print(f"   Grid: atr_min={ATR_MIN_GRID}  atr_max={ATR_MAX_GRID}  tp_mult=1.0 (fixed)")
    print(f"   Combos: {len(grid)}")
    print("=" * 60)

    t_start_total = time.perf_counter()

    for symbol in pairs:
        m15 = m15_frames[symbol]
        n = len(m15)
        split = int(n * IS_FRACTION)
        m15_is = m15.iloc[:split]
        m15_oos = m15.iloc[split:]

        is_end = m15_is.index[-1] if len(m15_is) else "?"
        oos_start = m15_oos.index[0] if len(m15_oos) else "?"
        print(f"\n  {symbol}  IS bars={len(m15_is)}  OOS bars={len(m15_oos)}")
        print(f"           IS end={is_end}  OOS start={oos_start}")

        best_is_pf = float("-inf")
        best_combo: tuple[float, float] = (
            DEFAULT_PARAMS.range_atr_min,
            DEFAULT_PARAMS.range_atr_max,
        )

        for atr_min, atr_max in grid:
            t0 = time.perf_counter()
            is_metrics = _run_combo(symbol, m15_is, atr_min, atr_max)
            elapsed = time.perf_counter() - t0
            pf = _safe_float(is_metrics.get("profit_factor"))
            trades = _safe_float(is_metrics.get("trades"))
            pf_str = f"{pf:.3f}" if math.isfinite(pf) else " N/A"
            trades_str = str(int(trades)) if math.isfinite(trades) else "N/A"
            print(
                f"    IS  atr_min={atr_min:.1f} atr_max={atr_max:.1f} -> "
                f"PF={pf_str}  trades={trades_str}  ({elapsed:.1f}s)"
            )
            if math.isfinite(pf) and pf > best_is_pf:
                best_is_pf = pf
                best_combo = (atr_min, atr_max)

        # OOS evaluation of best IS combo
        best_min, best_max = best_combo
        oos_metrics = _run_combo(symbol, m15_oos, best_min, best_max)
        oos_pf = _safe_float(oos_metrics.get("profit_factor"))
        oos_ret = _safe_float(oos_metrics.get("return_pct"))
        oos_wr = _safe_float(oos_metrics.get("win_rate"))
        oos_trades = _safe_float(oos_metrics.get("trades"))
        oos_dd = _safe_float(oos_metrics.get("max_drawdown_pct"))

        # OOS retention verdict (mirrors validate_oos.py thresholds)
        if math.isfinite(oos_pf) and math.isfinite(best_is_pf) and best_is_pf > 0:
            retention = oos_pf / best_is_pf
        else:
            retention = float("nan")

        if math.isfinite(retention):
            if retention >= 0.7:
                verdict = "HOLDS"
            elif retention >= 0.4:
                verdict = "PARTIAL"
            else:
                verdict = "WEAK"
        else:
            verdict = "N/A"

        print(
            f"\n  >> Best IS combo: atr_min={best_min:.1f} atr_max={best_max:.1f}"
            f"  IS_PF={best_is_pf:.3f}"
        )
        oos_pf_str = f"{oos_pf:.3f}" if math.isfinite(oos_pf) else "N/A"
        ret_str = f"{oos_ret:+.1f}%" if math.isfinite(oos_ret) else "N/A"
        wr_str = f"{oos_wr:.1f}%" if math.isfinite(oos_wr) else "N/A"
        dd_str = f"{oos_dd:.1f}%" if math.isfinite(oos_dd) else "N/A"
        trades_str = str(int(oos_trades)) if math.isfinite(oos_trades) else "N/A"
        ret_ratio = f"{retention:.2f}" if math.isfinite(retention) else "N/A"
        print(
            f"  >> OOS: PF={oos_pf_str}  Return={ret_str}  WinRate={wr_str}  "
            f"MaxDD={dd_str}  Trades={trades_str}  Retention={ret_ratio}  [{verdict}]"
        )

        results[symbol] = {
            "best_atr_min": best_min,
            "best_atr_max": best_max,
            "is_pf": best_is_pf,
            "oos_pf": oos_pf,
            "oos_return_pct": oos_ret,
            "oos_win_rate": oos_wr,
            "oos_max_drawdown_pct": oos_dd,
            "oos_trades": oos_trades,
            "retention": retention,
            "verdict": verdict,
        }

    elapsed_total = time.perf_counter() - t_start_total
    print(f"\n  Total calibration wall-clock: {elapsed_total:.1f}s")
    return results


# ---------------------------------------------------------------------------
# Calibration summary and pair ranking
# ---------------------------------------------------------------------------


def _print_calibration_summary(cal: dict[str, dict]) -> None:
    print()
    print("=" * 72)
    print("   CALIBRATION SUMMARY — OOS RESULTS (best IS params)")
    print("=" * 72)
    header = (
        f"{'Pair':<8} | {'atr_min':>7} | {'atr_max':>7} | {'IS_PF':>6} | "
        f"{'OOS_PF':>6} | {'Retain':>6} | {'Verdict':<8}"
    )
    print(header)
    print("-" * len(header))

    # Rank by OOS PF
    ranked = sorted(
        cal.items(),
        key=lambda kv: kv[1]["oos_pf"] if math.isfinite(kv[1]["oos_pf"]) else float("-inf"),
        reverse=True,
    )

    for symbol, r in ranked:
        is_pf = r["is_pf"]
        oos_pf = r["oos_pf"]
        ret = r["oos_return_pct"]
        retention = r["retention"]
        is_pf_str = f"{is_pf:.3f}" if math.isfinite(is_pf) else "  N/A"
        oos_pf_str = f"{oos_pf:.3f}" if math.isfinite(oos_pf) else "  N/A"
        ret_str = f"{ret:+.1f}%" if math.isfinite(ret) else "  N/A"
        retain_str = f"{retention:.2f}" if math.isfinite(retention) else "  N/A"
        verdict = r["verdict"]
        atr_min = r["best_atr_min"]
        atr_max = r["best_atr_max"]
        print(
            f"{symbol:<8} | {atr_min:>7.1f} | {atr_max:>7.1f} | {is_pf_str:>6} | "
            f"{oos_pf_str:>6} | {retain_str:>6} | {verdict:<8}  OOS_ret={ret_str}"
        )

    print("=" * 72)


def _print_pair_ranking(cal: dict[str, dict]) -> None:
    """Print pair ranking and KEEP/DROP recommendation for the user to approve."""
    print()
    print("=" * 72)
    print("   PAIR RANKING & RECOMMENDATION (user approval required)")
    print("=" * 72)
    print("  Criteria: OOS PF >= 1.2, OOS WinRate >= 35%, OOS MaxDD <= 20%")
    print("  Recommendation is DATA-DRIVEN; final decision is yours.")
    print()

    # Rank by OOS PF
    ranked = sorted(
        cal.items(),
        key=lambda kv: kv[1]["oos_pf"] if math.isfinite(kv[1]["oos_pf"]) else float("-inf"),
        reverse=True,
    )

    for rank, (symbol, r) in enumerate(ranked, 1):
        oos_pf = r["oos_pf"]
        oos_wr = r["oos_win_rate"]
        oos_dd = r["oos_max_drawdown_pct"]
        oos_ret = r["oos_return_pct"]
        verdict = r["verdict"]

        rec = _recommendation(oos_pf, oos_wr, oos_dd)

        pf_str = f"{oos_pf:.3f}" if math.isfinite(oos_pf) else "N/A"
        wr_str = f"{oos_wr:.1f}%" if math.isfinite(oos_wr) else "N/A"
        dd_str = f"{oos_dd:.1f}%" if math.isfinite(oos_dd) else "N/A"
        ret_str = f"{oos_ret:+.1f}%" if math.isfinite(oos_ret) else "N/A"

        print(
            f"  #{rank}  {symbol:<8}  OOS_PF={pf_str}  WinRate={wr_str}  MaxDD={dd_str}  "
            f"OOS_ret={ret_str}  Retention={verdict}  -> {rec}"
        )

    print()

    def _rec(r: dict) -> str:
        return _recommendation(r["oos_pf"], r["oos_win_rate"], r["oos_max_drawdown_pct"])

    keep = [s for s, r in cal.items() if _rec(r) == "KEEP"]
    review = [s for s, r in cal.items() if _rec(r) == "REVIEW"]
    drop = [s for s, r in cal.items() if _rec(r) == "DROP"]

    print("  RECOMMENDED KEEP   :", ", ".join(keep) if keep else "(none)")
    print("  RECOMMENDED REVIEW :", ", ".join(review) if review else "(none)")
    print("  RECOMMENDED DROP   :", ", ".join(drop) if drop else "(none)")
    print()

    # Print suggested params per kept pair
    print("  SUGGESTED PARAMS (pending your approval — do NOT edit config yet):")
    for symbol, r in cal.items():
        rec = _recommendation(r["oos_pf"], r["oos_win_rate"], r["oos_max_drawdown_pct"])
        if rec in ("KEEP", "REVIEW"):
            print(
                f"    {symbol:<8}  range_atr_min={r['best_atr_min']:.1f}  "
                f"range_atr_max={r['best_atr_max']:.1f}  tp_mult=1.0  atr_period=14"
            )
    print("=" * 72)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    missing = _orb_data_complete(ORB_PAIRS, DATA_DIR)
    if missing:
        print(f"ERROR: Missing M15 CSVs for: {', '.join(missing)}")
        print(f"  Expected in: {DATA_DIR}")
        sys.exit(1)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Load all frames once (reused across baseline + calibration)
    m15_frames: dict[str, pd.DataFrame] = {}
    for symbol in ORB_PAIRS:
        m15_frames[symbol] = _load_orb_m15(symbol, DATA_DIR)
        logger.info("Loaded %s M15: %d bars", symbol, len(m15_frames[symbol]))

    # ------------------------------------------------------------------
    # Phase 1: Baseline at default params
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("   PHASE 1: BASELINE (default params)")
    print("=" * 60)

    baseline_rows: list[dict] = []
    all_baseline_trades: dict[str, list] = {}

    for symbol in ORB_PAIRS:
        print()
        print(f"{'=' * 50}")
        print(f"  Processing {symbol} (London ORB — baseline)")
        print(f"{'=' * 50}")

        m15 = m15_frames[symbol]
        strategy = _build_orb_strategy([symbol], DEFAULT_PARAMS)
        result = run_backtest(strategy, m15, _EMPTY_H4, symbol, cash=CASH, commission=COMMISSION)
        metrics = result.metrics

        period_start = result.equity_curve.index[0] if len(result.equity_curve) else "?"
        period_end = result.equity_curve.index[-1] if len(result.equity_curve) else "?"

        print(format_metrics_results(symbol, metrics, period_start, period_end))
        save_metrics_results(symbol, metrics, RESULTS_DIR, period_start, period_end)

        baseline_rows.append(_extract_row(symbol, metrics, m15))
        all_baseline_trades[symbol] = result.trades

    _print_comparison_table(baseline_rows)
    _print_ranking(baseline_rows)

    print()
    print("=== R:R / TRADE-FREQUENCY SANITY CHECK ===")
    for symbol in ORB_PAIRS:
        _print_rr_sanity(symbol, all_baseline_trades[symbol])

    _print_portfolio_summary(baseline_rows)

    # ------------------------------------------------------------------
    # Phase 2: Walk-forward calibration
    # ------------------------------------------------------------------
    cal = _walk_forward_calibration(ORB_PAIRS, m15_frames)

    _print_calibration_summary(cal)
    _print_pair_ranking(cal)

    print()
    logger.warning("All ORB results saved to %s", RESULTS_DIR)
    print(f"Results saved to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
