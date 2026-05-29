"""Run Asian Session Scalper backtests across all Asian pairs and produce a comparison report.

Flow:
  1. Ensure CSV data exists in backtest/data/ — generates synthetic data if missing.
  2. For each pair: load H4 + M15 → prepare → run → print individual results → save JSON.
  3. Print a side-by-side comparison table ranked by net return.
  4. Print an overall portfolio summary with keep/review/drop recommendations.

Usage:
    python backtest/run_asian.py
"""

from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.asian_engine import (
    ASIAN_PAIRS,
    format_results,
    prepare_asian_data,
    run_asian_backtest,
    save_results,
)
from backtest.download_data import download_asian_all, load_asian_data
from drift.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results_asian"

CASH: float = 500.0
COMMISSION: float = 0.00007


def _asian_data_complete(pairs: list[str], data_dir: Path) -> bool:
    for symbol in pairs:
        if not (data_dir / f"{symbol}_H4.csv").exists():
            return False
        if not (data_dir / f"{symbol}_M15.csv").exists():
            return False
    return True


def _safe_float(val: object) -> float:
    try:
        f = float(val)  # type: ignore[arg-type]
        return f if math.isfinite(f) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _recommendation(profit_factor: float, win_rate: float, max_drawdown_pct: float) -> str:
    """Classify a pair based on three criteria."""
    keep_pf = profit_factor >= 1.2
    keep_wr = win_rate >= 35.0
    keep_dd = abs(max_drawdown_pct) <= 20.0

    fails = sum([not keep_pf, not keep_wr, not keep_dd])
    if fails == 0:
        return "KEEP"
    if fails == 1:
        return "REVIEW"
    return "DROP"


def _print_comparison_table(rows: list[dict]) -> None:
    cols = (
        f"{'Pair':<8} | {'Return%':>7} | {'WinRate':>7} | {'PF':>5}"
        f" | {'MaxDD%':>7} | {'Trades':>6} | {'Sharpe':>6}"
    )
    header = cols
    sep = "-" * len(header)
    print()
    print("=" * len(header))
    print("   ASIAN SESSION SCALPER BACKTEST — ALL PAIRS")
    print("=" * len(header))

    starts = [r["start"] for r in rows if r["start"] != "?"]
    ends = [r["end"] for r in rows if r["end"] != "?"]
    period_start = min(starts) if starts else "?"
    period_end = max(ends) if ends else "?"
    print(f"Period: {period_start} to {period_end}")
    print()
    print(header)
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
    print("=" * len(header))


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
    print("=== PAIR RECOMMENDATIONS ===")
    for r in rows:
        rec = _recommendation(r["profit_factor"], r["win_rate"], r["max_drawdown_pct"])
        print(f"  {r['symbol']:<8}  {rec}")


def main() -> None:
    config = load_config()
    strategy = config.strategy

    if not _asian_data_complete(ASIAN_PAIRS, DATA_DIR):
        logger.info("Asian data missing — running download_asian_all()")
        download_asian_all(ASIAN_PAIRS, DATA_DIR)
    else:
        logger.info("All Asian CSV files present in %s", DATA_DIR)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict] = []

    for symbol in ASIAN_PAIRS:
        print()
        print(f"{'=' * 50}")
        print(f"  Processing {symbol} (Asian Session Scalper)")
        print(f"{'=' * 50}")

        df_h4, df_m15 = load_asian_data(symbol, DATA_DIR)
        df_bt = prepare_asian_data(df_h4, df_m15)

        stats, _bt = run_asian_backtest(
            df_bt,
            cash=CASH,
            commission=COMMISSION,
            sl_atr_mult=strategy.sl_atr_mult,
            adx_max_threshold=strategy.adx_max_threshold,
            rsi_oversold=strategy.rsi_oversold,
            rsi_overbought=strategy.rsi_overbought,
            range_atr_min=strategy.range_atr_min,
            range_atr_max=strategy.range_atr_max,
        )

        print(format_results(symbol, stats))
        save_results(symbol, stats, RESULTS_DIR)

        summary_rows.append(
            {
                "symbol": symbol,
                "return_pct": _safe_float(stats.get("Return [%]")),
                "win_rate": _safe_float(stats.get("Win Rate [%]")),
                "profit_factor": _safe_float(stats.get("Profit Factor")),
                "max_drawdown_pct": _safe_float(stats.get("Max. Drawdown [%]")),
                "trades": _safe_float(stats.get("# Trades")),
                "sharpe": _safe_float(stats.get("Sharpe Ratio")),
                "start": str(stats.get("Start", "?")),
                "end": str(stats.get("End", "?")),
            }
        )

    _print_comparison_table(summary_rows)
    _print_ranking(summary_rows)
    _print_portfolio_summary(summary_rows)

    print()
    logger.info("All Asian results saved to %s", RESULTS_DIR)


if __name__ == "__main__":
    main()
