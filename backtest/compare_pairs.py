"""Drift pair comparison — deeper analysis tool beyond run_all.py.

Loads saved backtest results from JSON or runs fresh backtests, then produces:
  - Individual pair scorecards with weighted composite score
  - Correlation matrix of daily returns
  - Final ranking with KEEP / REVIEW / DROP verdicts
  - Actionable recommendations including correlated-pair warnings

Usage:
    python backtest/compare_pairs.py
"""

from __future__ import annotations

import json
import logging
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.download_data import PAIRS, download_all, load_data
from backtest.engine_meanrev_legacy import (
    format_results,
    prepare_backtest_data,
    run_backtest,
    save_results,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"

CASH: float = 500.0
COMMISSION: float = 0.00007
SL_ATR_MULT: float = 1.5
TP_RATIO: float = 2.0

WEIGHTS = {
    "profit_factor": 0.30,
    "win_rate": 0.20,
    "sharpe": 0.20,
    "max_drawdown": 0.15,
    "trades": 0.15,
}

CORR_HIGH_THRESHOLD: float = 0.70

VERDICT_KEEP = "KEEP"
VERDICT_REVIEW = "REVIEW"
VERDICT_DROP = "DROP"


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def _safe_float(val: object) -> float:
    try:
        f = float(val)  # type: ignore[arg-type]
        return f if math.isfinite(f) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _data_complete(pairs: list[str], data_dir: Path) -> bool:
    return all(
        (data_dir / f"{sym}_D1.csv").exists() and (data_dir / f"{sym}_H4.csv").exists()
        for sym in pairs
    )


def _load_json_results(symbol: str, results_dir: Path) -> pd.Series | None:
    path = results_dir / f"{symbol}.json"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    stats = payload.get("stats", {})
    return pd.Series(stats)


def _run_fresh(symbol: str, data_dir: Path, results_dir: Path) -> pd.Series:
    logger.info("Running fresh backtest for %s", symbol)
    df_d1, df_h4 = load_data(symbol, data_dir)
    df_bt = prepare_backtest_data(df_d1, df_h4)
    stats, _bt = run_backtest(
        df_bt, cash=CASH, commission=COMMISSION, sl_atr_mult=SL_ATR_MULT, tp_ratio=TP_RATIO
    )
    print(format_results(symbol, stats))
    save_results(symbol, stats, results_dir)
    return stats


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_or_run_backtests(
    pairs: list[str], data_dir: Path, results_dir: Path
) -> dict[str, pd.Series]:
    """Load existing results or run fresh backtests. Returns {pair: stats}."""
    if not _data_complete(pairs, data_dir):
        logger.info("CSV data missing — generating")
        download_all(pairs, data_dir)

    results_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, pd.Series] = {}

    for symbol in pairs:
        cached = _load_json_results(symbol, results_dir)
        if cached is not None:
            logger.info("Loaded cached results for %s", symbol)
            out[symbol] = cached
        else:
            out[symbol] = _run_fresh(symbol, data_dir, results_dir)

    return out


def compute_composite_score(stats: pd.Series) -> dict:
    """Compute weighted composite score from backtest stats.

    Returns a dict with keys: metric scores (0-100 each) and 'total' (0-100).
    """
    pf = _safe_float(stats.get("Profit Factor"))
    wr = _safe_float(stats.get("Win Rate [%]"))
    sharpe = _safe_float(stats.get("Sharpe Ratio"))
    dd = _safe_float(stats.get("Max. Drawdown [%]"))
    trades = _safe_float(stats.get("# Trades"))

    pf_score = min(pf / 2.0, 1.0) * 100 if not math.isnan(pf) else 0.0
    wr_score = wr if not math.isnan(wr) else 0.0
    sharpe_score = min(max(sharpe + 1, 0), 3) / 3 * 100 if not math.isnan(sharpe) else 0.0
    dd_score = max(0.0, 100 - abs(dd) * 4) if not math.isnan(dd) else 0.0
    trades_score = min(trades / 100, 1.0) * 100 if not math.isnan(trades) else 0.0

    total = (
        pf_score * WEIGHTS["profit_factor"]
        + wr_score * WEIGHTS["win_rate"]
        + sharpe_score * WEIGHTS["sharpe"]
        + dd_score * WEIGHTS["max_drawdown"]
        + trades_score * WEIGHTS["trades"]
    )

    return {
        "pf_score": round(pf_score, 1),
        "wr_score": round(wr_score, 1),
        "sharpe_score": round(sharpe_score, 1),
        "dd_score": round(dd_score, 1),
        "trades_score": round(trades_score, 1),
        "total": round(total, 1),
    }


def pair_correlation_matrix(pairs: list[str], data_dir: Path) -> pd.DataFrame:
    """Compute correlation matrix of daily returns between pairs.

    Uses H4 close prices resampled to daily frequency. Pairs with missing
    CSV data are skipped; their row/column will be NaN.
    """
    daily_returns: dict[str, pd.Series] = {}

    for symbol in pairs:
        path_h4 = data_dir / f"{symbol}_H4.csv"
        if not path_h4.exists():
            logger.warning("H4 CSV missing for %s — skipping from correlation", symbol)
            continue

        df = pd.read_csv(path_h4, index_col="time", parse_dates=True)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        close_col = "close" if "close" in df.columns else "Close"
        daily_close = df[close_col].resample("D").last().dropna()
        daily_returns[symbol] = daily_close.pct_change().dropna()

    if not daily_returns:
        return pd.DataFrame(index=pairs, columns=pairs, dtype=float)

    returns_df = pd.DataFrame(daily_returns)
    corr = returns_df.corr()

    # Ensure all pairs appear even if data was missing
    corr = corr.reindex(index=pairs, columns=pairs)
    return corr


def _verdict(score: float) -> str:
    if score >= 60:
        return VERDICT_KEEP
    if score >= 40:
        return VERDICT_REVIEW
    return VERDICT_DROP


def generate_comparison_report(results: dict[str, pd.Series], data_dir: Path) -> str:
    """Generate a comprehensive text report comparing all pairs."""
    scores: dict[str, dict] = {
        sym: compute_composite_score(stats) for sym, stats in results.items()
    }
    corr = pair_correlation_matrix(list(results.keys()), data_dir)

    ranked = sorted(results.keys(), key=lambda s: scores[s]["total"], reverse=True)

    width = 44
    lines: list[str] = []

    lines.append("=" * width)
    lines.append("    DRIFT PAIR ANALYSIS — DETAILED REPORT")
    lines.append("=" * width)

    # ------------------------------------------------------------------
    # Individual scorecards
    # ------------------------------------------------------------------
    lines.append("")
    lines.append("=== INDIVIDUAL SCORECARDS ===")

    for sym in ranked:
        stats = results[sym]
        sc = scores[sym]

        pf = _safe_float(stats.get("Profit Factor"))
        wr = _safe_float(stats.get("Win Rate [%]"))
        sharpe = _safe_float(stats.get("Sharpe Ratio"))
        dd = _safe_float(stats.get("Max. Drawdown [%]"))
        trades = _safe_float(stats.get("# Trades"))

        pf_str = f"{pf:.2f}" if not math.isnan(pf) else "N/A"
        wr_str = f"{wr:.1f}%" if not math.isnan(wr) else "N/A"
        sharpe_str = f"{sharpe:.2f}" if not math.isnan(sharpe) else "N/A"
        dd_str = f"{dd:.1f}%" if not math.isnan(dd) else "N/A"
        trades_str = str(int(trades)) if not math.isnan(trades) else "N/A"

        lines.append("")
        lines.append(f"{sym} — Composite Score: {sc['total']}/100")
        lines.append(f"  Profit Factor:  {pf_str:<6}  (score: {sc['pf_score']})")
        lines.append(f"  Win Rate:       {wr_str:<6}  (score: {sc['wr_score']})")
        lines.append(f"  Sharpe Ratio:   {sharpe_str:<6}  (score: {sc['sharpe_score']})")
        lines.append(f"  Max Drawdown:   {dd_str:<6}  (score: {sc['dd_score']})")
        lines.append(f"  Trade Count:    {trades_str:<6}  (score: {sc['trades_score']})")

    # ------------------------------------------------------------------
    # Correlation matrix
    # ------------------------------------------------------------------
    lines.append("")
    lines.append("=== PAIR CORRELATION MATRIX ===")

    if not corr.empty and not corr.isnull().all().all():
        col_w = 7
        header_row = " " * 8 + "".join(f"{p:>{col_w}}" for p in ranked)
        lines.append(header_row)

        for sym in ranked:
            row_vals = []
            for other in ranked:
                in_matrix = sym in corr.index and other in corr.columns
                val = corr.loc[sym, other] if in_matrix else float("nan")
                row_vals.append(
                    f"{val:>{col_w}.2f}" if not math.isnan(val) else f"{'N/A':>{col_w}}"
                )
            lines.append(f"{sym:<8}" + "".join(row_vals))

        high_pairs: list[str] = []
        seen: set[frozenset] = set()
        for i, sym_a in enumerate(ranked):
            for sym_b in ranked[i + 1 :]:
                key = frozenset([sym_a, sym_b])
                if key in seen:
                    continue
                seen.add(key)
                if sym_a in corr.index and sym_b in corr.columns:
                    val = corr.loc[sym_a, sym_b]
                    if not math.isnan(val) and abs(val) > CORR_HIGH_THRESHOLD:
                        high_pairs.append(f"{sym_a}-{sym_b}")

        lines.append("")
        if high_pairs:
            joined = ", ".join(high_pairs)
            lines.append(f"Highly correlated pairs (>{CORR_HIGH_THRESHOLD}): {joined}")
            lines.append("Consider reducing exposure to correlated groups.")
        else:
            lines.append("No highly correlated pairs found.")
    else:
        lines.append("  (correlation data unavailable)")

    # ------------------------------------------------------------------
    # Final ranking table
    # ------------------------------------------------------------------
    lines.append("")
    lines.append("=== FINAL RANKING ===")
    lines.append(f"{'Rank':>4} | {'Pair':<8} | {'Score':>5} | {'Verdict'}")
    lines.append("-" * 32)

    for rank, sym in enumerate(ranked, 1):
        verdict = _verdict(scores[sym]["total"])
        lines.append(f"{rank:>4} | {sym:<8} | {scores[sym]['total']:>5.1f} | {verdict}")

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------
    lines.append("")
    lines.append("=== RECOMMENDATIONS ===")

    keep = [s for s in ranked if _verdict(scores[s]["total"]) == VERDICT_KEEP]
    review = [s for s in ranked if _verdict(scores[s]["total"]) == VERDICT_REVIEW]
    drop = [s for s in ranked if _verdict(scores[s]["total"]) == VERDICT_DROP]

    keep_str = ", ".join(keep) if keep else "none"
    review_str = ", ".join(review) if review else "none"
    drop_str = ", ".join(drop) if drop else "none"

    lines.append(f"• Keep:   {keep_str} (score >= 60)")
    lines.append(f"• Review: {review_str} (score 40-60)")
    lines.append(f"• Drop:   {drop_str} (score < 40)")

    if not corr.empty and not corr.isnull().all().all():
        seen_warn: set[frozenset] = set()
        for sym_a in keep:
            for sym_b in keep:
                if sym_a == sym_b:
                    continue
                key = frozenset([sym_a, sym_b])
                if key in seen_warn:
                    continue
                seen_warn.add(key)
                if sym_a in corr.index and sym_b in corr.columns:
                    val = corr.loc[sym_a, sym_b]
                    if not math.isnan(val) and abs(val) > CORR_HIGH_THRESHOLD:
                        higher = (
                            sym_a if scores[sym_a]["total"] >= scores[sym_b]["total"] else sym_b
                        )
                        lines.append(
                            f"• Note: {sym_a} and {sym_b} are highly correlated"
                            f" ({val:.2f}) — consider keeping only {higher}"
                        )

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    results = load_or_run_backtests(PAIRS, DATA_DIR, RESULTS_DIR)
    report = generate_comparison_report(results, DATA_DIR)
    print(report)
    logger.info("Comparison complete")


if __name__ == "__main__":
    main()
