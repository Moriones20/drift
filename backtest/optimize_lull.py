"""Daily Lull Scalper parameter optimization — grid search via Backtesting.py's bt.optimize().

Flow:
  1. Load or generate CSV data for all 6 lull pairs.
  2. For each pair run bt.optimize() over the full parameter grid.
  3. Print per-pair best parameters and key metrics.
  4. Print a side-by-side comparison table.
  5. Recommend which pairs to KEEP (PF > 1.5, WinRate > 50%, MaxDD > -5%, Trades > 20).
  6. Find "universal" parameters: the combo that maximises total equity across all kept pairs.
  7. Print final YAML-like config block.

Usage:
    python backtest/optimize_lull.py
"""

from __future__ import annotations

import logging
import math
import sys
from itertools import product
from pathlib import Path

import pandas as pd
from backtesting import Backtest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.download_data import LULL_PAIRS, download_lull_all, load_lull_data
from backtest.lull_engine import DailyLullStrategy, prepare_lull_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results_lull_opt"

CASH: float = 500.0
COMMISSION: float = 0.00007

# ---------------------------------------------------------------------------
# Parameter space
# ---------------------------------------------------------------------------

SL_ATR_MULT = [1.0, 1.25, 1.5, 2.0, 2.5]
ADX_MAX_THRESHOLD = [20.0, 25.0, 30.0, 35.0]
RSI_OVERSOLD = [25.0, 30.0, 35.0]
RSI_OVERBOUGHT = [65.0, 70.0, 75.0]
RANGE_ATR_MIN = [0.5, 0.75, 1.0]
RANGE_ATR_MAX = [2.5, 3.0, 4.0]

# Thresholds for keeping a pair
KEEP_MIN_PF: float = 1.5
KEEP_MIN_WINRATE: float = 50.0
KEEP_MAX_DD: float = -5.0  # MaxDD is negative, so we need abs < 5 → val > -5
KEEP_MIN_TRADES: int = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(val: object) -> float:
    """Return float(val) or nan on error/infinity."""
    try:
        f = float(val)  # type: ignore[arg-type]
        return f if math.isfinite(f) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _lull_data_complete(pairs: list[str], data_dir: Path) -> bool:
    """Return True if all H4 and M15 CSV files exist for every pair."""
    for symbol in pairs:
        if not (data_dir / f"{symbol}_H4.csv").exists():
            return False
        if not (data_dir / f"{symbol}_M15.csv").exists():
            return False
    return True


def _passes_keep_criteria(pf: float, wr: float, max_dd: float, trades: float) -> bool:
    """Return True if a pair meets all keep thresholds."""
    if not all(math.isfinite(v) for v in (pf, wr, max_dd, trades)):
        return False
    return (
        pf >= KEEP_MIN_PF
        and wr >= KEEP_MIN_WINRATE
        and max_dd > KEEP_MAX_DD
        and trades >= KEEP_MIN_TRADES
    )


def _extract_metrics(stats: pd.Series) -> dict:
    """Extract the key metric dict from a bt.run() / bt.optimize() result."""
    return {
        "return_pct": _safe_float(stats.get("Return [%]")),
        "win_rate": _safe_float(stats.get("Win Rate [%]")),
        "profit_factor": _safe_float(stats.get("Profit Factor")),
        "max_drawdown_pct": _safe_float(stats.get("Max. Drawdown [%]")),
        "trades": _safe_float(stats.get("# Trades")),
        "sharpe": _safe_float(stats.get("Sharpe Ratio")),
        "equity_final": _safe_float(stats.get("Equity Final [$]")),
    }


def _extract_params(stats: pd.Series) -> dict:
    """Pull chosen parameters off the _strategy attribute of an optimize() result."""
    s = stats._strategy
    return {
        "sl_atr_mult": getattr(s, "sl_atr_mult", float("nan")),
        "adx_max_threshold": getattr(s, "adx_max_threshold", float("nan")),
        "rsi_oversold": getattr(s, "rsi_oversold", float("nan")),
        "rsi_overbought": getattr(s, "rsi_overbought", float("nan")),
        "range_atr_min": getattr(s, "range_atr_min", float("nan")),
        "range_atr_max": getattr(s, "range_atr_max", float("nan")),
    }


# ---------------------------------------------------------------------------
# Per-pair optimization
# ---------------------------------------------------------------------------


def optimize_pair(symbol: str, df: pd.DataFrame) -> dict | None:
    """Run bt.optimize() for one pair and return a result dict, or None on failure."""
    logger.info("Optimizing %s — grid: 5×4×3×3×3×3 = 1620 combos", symbol)

    bt = Backtest(
        df,
        DailyLullStrategy,
        cash=CASH,
        commission=COMMISSION,
        exclusive_orders=True,
    )

    try:
        stats = bt.optimize(
            sl_atr_mult=SL_ATR_MULT,
            adx_max_threshold=ADX_MAX_THRESHOLD,
            rsi_oversold=RSI_OVERSOLD,
            rsi_overbought=RSI_OVERBOUGHT,
            range_atr_min=RANGE_ATR_MIN,
            range_atr_max=RANGE_ATR_MAX,
            maximize="Equity Final [$]",
            constraint=lambda p: (
                p.rsi_oversold < p.rsi_overbought and p.range_atr_min < p.range_atr_max
            ),
            return_heatmap=False,
        )
    except Exception as exc:
        logger.warning("Optimization failed for %s: %s", symbol, exc)
        return None

    trades = _safe_float(stats.get("# Trades"))
    if not math.isfinite(trades) or trades == 0:
        logger.warning("No trades found during optimization for %s — skipping", symbol)
        return None

    metrics = _extract_metrics(stats)
    params = _extract_params(stats)
    keep = _passes_keep_criteria(
        metrics["profit_factor"],
        metrics["win_rate"],
        metrics["max_drawdown_pct"],
        metrics["trades"],
    )

    return {
        "symbol": symbol,
        "params": params,
        "metrics": metrics,
        "keep": keep,
    }


# ---------------------------------------------------------------------------
# Universal parameter search
# ---------------------------------------------------------------------------


def _build_reduced_grid(pair_results: list[dict]) -> list[dict[str, float]]:
    """Build a reduced grid from the top-3 most-frequent optimal values per param.

    For each parameter we collect every optimal value chosen across kept pairs
    and take the union.  If fewer than 3 pairs kept, fall back to the full
    parameter lists so we still have a reasonable search space.
    """
    param_names = [
        "sl_atr_mult",
        "adx_max_threshold",
        "rsi_oversold",
        "rsi_overbought",
        "range_atr_min",
        "range_atr_max",
    ]
    full_space = {
        "sl_atr_mult": SL_ATR_MULT,
        "adx_max_threshold": ADX_MAX_THRESHOLD,
        "rsi_oversold": RSI_OVERSOLD,
        "rsi_overbought": RSI_OVERBOUGHT,
        "range_atr_min": RANGE_ATR_MIN,
        "range_atr_max": RANGE_ATR_MAX,
    }

    reduced: dict[str, list[float]] = {}
    for name in param_names:
        seen: list[float] = []
        for r in pair_results:
            v = r["params"].get(name)
            if v is not None and math.isfinite(float(v)) and v not in seen:
                seen.append(float(v))
        # Supplement with full space values (up to 3 unique values per param)
        for v in full_space[name]:
            if v not in seen:
                seen.append(v)
            if len(seen) >= 3:
                break
        reduced[name] = seen[:3]

    combos: list[dict[str, float]] = []
    for combo in product(
        reduced["sl_atr_mult"],
        reduced["adx_max_threshold"],
        reduced["rsi_oversold"],
        reduced["rsi_overbought"],
        reduced["range_atr_min"],
        reduced["range_atr_max"],
    ):
        sl, adx, rsi_os, rsi_ob, rng_min, rng_max = combo
        if rsi_os < rsi_ob and rng_min < rng_max:
            combos.append(
                {
                    "sl_atr_mult": sl,
                    "adx_max_threshold": adx,
                    "rsi_oversold": rsi_os,
                    "rsi_overbought": rsi_ob,
                    "range_atr_min": rng_min,
                    "range_atr_max": rng_max,
                }
            )
    return combos


def find_universal_params(
    kept_results: list[dict],
    pair_dfs: dict[str, pd.DataFrame],
) -> dict | None:
    """Brute-force search over a reduced grid to find params that maximise total equity.

    Returns a dict with keys 'params', 'per_pair_metrics', and 'total_equity'.
    """
    if not kept_results:
        return None

    combos = _build_reduced_grid(kept_results)
    logger.info(
        "Universal search: %d combos × %d kept pairs = %d runs",
        len(combos),
        len(kept_results),
        len(combos) * len(kept_results),
    )

    best_equity: float = float("-inf")
    best_params: dict | None = None
    best_per_pair: dict[str, dict] | None = None

    for combo in combos:
        total_equity: float = 0.0
        per_pair: dict[str, dict] = {}

        for r in kept_results:
            symbol = r["symbol"]
            df = pair_dfs[symbol]

            bt = Backtest(
                df,
                DailyLullStrategy,
                cash=CASH,
                commission=COMMISSION,
                exclusive_orders=True,
            )
            try:
                stats = bt.run(**combo)
            except Exception:
                total_equity = float("-inf")
                break

            eq = _safe_float(stats.get("Equity Final [$]"))
            if not math.isfinite(eq):
                total_equity = float("-inf")
                break

            total_equity += eq
            per_pair[symbol] = _extract_metrics(stats)

        if total_equity > best_equity:
            best_equity = total_equity
            best_params = combo
            best_per_pair = per_pair

    if best_params is None:
        return None

    return {
        "params": best_params,
        "per_pair_metrics": best_per_pair,
        "total_equity": best_equity,
    }


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------


def _print_pair_result(result: dict) -> None:
    """Print one pair's optimization summary."""
    sym = result["symbol"]
    m = result["metrics"]
    p = result["params"]
    keep_str = "KEEP" if result["keep"] else "DROP"

    print()
    print(f"  {'-' * 56}")
    print(f"  {sym}  ->  {keep_str}")
    print(f"  {'-' * 56}")
    print(
        f"  Return: {m['return_pct']:+.2f}%   WinRate: {m['win_rate']:.1f}%"
        f"   PF: {m['profit_factor']:.2f}   MaxDD: {m['max_drawdown_pct']:.1f}%"
        f"   Trades: {int(m['trades'])}   Sharpe: {m['sharpe']:.2f}"
    )
    print(
        f"  Params -> sl_atr_mult={p['sl_atr_mult']}  adx_max={p['adx_max_threshold']}"
        f"  rsi_os={p['rsi_oversold']}  rsi_ob={p['rsi_overbought']}"
        f"  range=[{p['range_atr_min']}, {p['range_atr_max']}]"
    )


def _print_comparison_table(results: list[dict]) -> None:
    """Print all pairs side by side, sorted by equity final descending."""
    header = (
        f"{'Pair':<8} | {'Return%':>7} | {'WinRate':>7} | {'PF':>5}"
        f" | {'MaxDD%':>7} | {'Trades':>6} | {'Sharpe':>6} | {'Status':<6}"
    )
    sep = "-" * len(header)

    print()
    print("=" * len(header))
    print("   OPTIMIZATION RESULTS — ALL PAIRS (best params per pair)")
    print("=" * len(header))
    print(header)
    print(sep)

    sorted_results = sorted(
        results,
        key=lambda r: (
            r["metrics"]["equity_final"]
            if math.isfinite(r["metrics"]["equity_final"])
            else float("-inf")
        ),
        reverse=True,
    )

    for r in sorted_results:
        m = r["metrics"]
        ret = f"{m['return_pct']:+.1f}%" if math.isfinite(m["return_pct"]) else "  N/A"
        wr = f"{m['win_rate']:.1f}%" if math.isfinite(m["win_rate"]) else "  N/A"
        pf = f"{m['profit_factor']:.2f}" if math.isfinite(m["profit_factor"]) else " N/A"
        dd = f"{m['max_drawdown_pct']:.1f}%" if math.isfinite(m["max_drawdown_pct"]) else "  N/A"
        tr = str(int(m["trades"])) if math.isfinite(m["trades"]) else "N/A"
        sh = f"{m['sharpe']:.2f}" if math.isfinite(m["sharpe"]) else "  N/A"
        status = "KEEP" if r["keep"] else "DROP"

        print(
            f"{r['symbol']:<8} | {ret:>7} | {wr:>7} | {pf:>5}"
            f" | {dd:>7} | {tr:>6} | {sh:>6} | {status:<6}"
        )

    print("=" * len(header))


def _print_universal_comparison(
    kept_results: list[dict],
    universal: dict,
) -> None:
    """Print individual-optimal vs universal-params comparison for kept pairs."""
    per_pair = universal["per_pair_metrics"] or {}
    header = (
        f"{'Pair':<8} | {'Indiv Return%':>13} | {'Univ Return%':>12}"
        f" | {'Indiv PF':>8} | {'Univ PF':>7}"
    )
    sep = "-" * len(header)

    print()
    print("=" * len(header))
    print("   INDIVIDUAL OPTIMAL vs UNIVERSAL PARAMETERS")
    print("=" * len(header))
    print(header)
    print(sep)

    for r in kept_results:
        sym = r["symbol"]
        im = r["metrics"]
        um = per_pair.get(sym, {})

        ir = f"{im['return_pct']:+.1f}%" if math.isfinite(im["return_pct"]) else "  N/A"
        ur_v = um.get("return_pct", float("nan"))
        ur = f"{ur_v:+.1f}%" if math.isfinite(ur_v) else "  N/A"
        ipf = f"{im['profit_factor']:.2f}" if math.isfinite(im["profit_factor"]) else " N/A"
        upf_v = um.get("profit_factor", float("nan"))
        upf = f"{upf_v:.2f}" if math.isfinite(upf_v) else " N/A"

        print(f"{sym:<8} | {ir:>13} | {ur:>12} | {ipf:>8} | {upf:>7}")

    print("=" * len(header))
    print(
        f"  Total equity with universal params: ${universal['total_equity']:.2f}"
        f"  (combined across {len(kept_results)} pairs)"
    )


def _print_recommendations(results: list[dict]) -> None:
    """Print keep/drop recommendation summary."""
    kept = [r for r in results if r["keep"]]
    dropped = [r for r in results if not r["keep"]]

    print()
    print("=== PAIR RECOMMENDATIONS ===")
    print(
        f"  Criteria: PF > {KEEP_MIN_PF}  |  WinRate > {KEEP_MIN_WINRATE}%"
        f"  |  MaxDD > {KEEP_MAX_DD}%  |  Trades >= {KEEP_MIN_TRADES}"
    )
    print()

    if kept:
        print("  KEEP:")
        for r in kept:
            m = r["metrics"]
            print(
                f"    {r['symbol']:<8}  PF={m['profit_factor']:.2f}"
                f"  WR={m['win_rate']:.1f}%  DD={m['max_drawdown_pct']:.1f}%"
                f"  Trades={int(m['trades'])}"
            )
    else:
        print("  KEEP: (none met the criteria)")

    if dropped:
        print()
        print("  DROP:")
        for r in dropped:
            m = r["metrics"]
            pf_s = f"{m['profit_factor']:.2f}" if math.isfinite(m["profit_factor"]) else "N/A"
            wr_s = f"{m['win_rate']:.1f}%" if math.isfinite(m["win_rate"]) else "N/A"
            dd_val = m["max_drawdown_pct"]
            dd_s = f"{dd_val:.1f}%" if math.isfinite(dd_val) else "N/A"
            tr_s = str(int(m["trades"])) if math.isfinite(m["trades"]) else "N/A"
            print(f"    {r['symbol']:<8}  PF={pf_s}  WR={wr_s}  DD={dd_s}  Trades={tr_s}")


def _print_final_config(kept_results: list[dict], universal: dict | None) -> None:
    """Print a YAML-like config block with recommended settings."""
    print()
    print("=== RECOMMENDED CONFIG ===")
    print()

    if universal and universal.get("params"):
        p = universal["params"]
        print("# Universal parameters (optimal across all kept pairs)")
        print("daily_lull_scalper:")
        print(f"  sl_atr_mult: {p['sl_atr_mult']}")
        print(f"  adx_max_threshold: {p['adx_max_threshold']}")
        print(f"  rsi_oversold: {p['rsi_oversold']}")
        print(f"  rsi_overbought: {p['rsi_overbought']}")
        print(f"  range_atr_min: {p['range_atr_min']}")
        print(f"  range_atr_max: {p['range_atr_max']}")
        print()

    if kept_results:
        print("# Per-pair optimal parameters (use if running pairs independently)")
        print("daily_lull_scalper_per_pair:")
        for r in kept_results:
            p = r["params"]
            print(f"  {r['symbol']}:")
            print(f"    sl_atr_mult: {p['sl_atr_mult']}")
            print(f"    adx_max_threshold: {p['adx_max_threshold']}")
            print(f"    rsi_oversold: {p['rsi_oversold']}")
            print(f"    rsi_overbought: {p['rsi_overbought']}")
            print(f"    range_atr_min: {p['range_atr_min']}")
            print(f"    range_atr_max: {p['range_atr_max']}")

    pairs_to_trade = [r["symbol"] for r in kept_results]
    print()
    print(f"  pairs: {pairs_to_trade}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point: optimize all pairs, find universal params, print report."""
    if not _lull_data_complete(LULL_PAIRS, DATA_DIR):
        logger.info("lull data missing — generating synthetic data")
        download_lull_all(LULL_PAIRS, DATA_DIR)
    else:
        logger.info("All lull CSV files present in %s", DATA_DIR)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # ---- Load data for all pairs ----
    pair_dfs: dict[str, pd.DataFrame] = {}
    for symbol in LULL_PAIRS:
        logger.info("Loading data for %s", symbol)
        df_h4, df_m15 = load_lull_data(symbol, DATA_DIR)
        pair_dfs[symbol] = prepare_lull_data(df_h4, df_m15)

    # ---- Per-pair optimization ----
    print()
    print("=" * 60)
    print("  DAILY LULL SCALPER — PARAMETER OPTIMIZATION")
    print(
        f"  Grid: {len(SL_ATR_MULT)}×{len(ADX_MAX_THRESHOLD)}×{len(RSI_OVERSOLD)}"
        f"×{len(RSI_OVERBOUGHT)}×{len(RANGE_ATR_MIN)}×{len(RANGE_ATR_MAX)}"
        f" = 1620 combos per pair"
    )
    print("=" * 60)

    all_results: list[dict] = []

    for symbol in LULL_PAIRS:
        result = optimize_pair(symbol, pair_dfs[symbol])
        if result is None:
            logger.warning("Skipping %s — optimization returned no result", symbol)
            continue
        all_results.append(result)
        _print_pair_result(result)

    if not all_results:
        logger.error("No pairs produced valid optimization results — aborting")
        return

    # ---- Comparison table ----
    _print_comparison_table(all_results)

    # ---- Recommendations ----
    _print_recommendations(all_results)

    # ---- Universal parameter search ----
    kept_results = [r for r in all_results if r["keep"]]

    if not kept_results:
        print()
        print("No pairs passed the keep criteria — universal parameter search skipped.")
        _print_final_config([], None)
        return

    print()
    logger.info(
        "Running universal parameter search over %d kept pair(s): %s",
        len(kept_results),
        [r["symbol"] for r in kept_results],
    )
    universal = find_universal_params(kept_results, pair_dfs)

    if universal:
        _print_universal_comparison(kept_results, universal)

    _print_final_config(kept_results, universal)

    print()
    logger.info("Optimization complete. Results saved to %s", RESULTS_DIR)


if __name__ == "__main__":
    main()
