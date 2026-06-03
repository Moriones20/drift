"""Out-of-sample validation for the Daily Lull Scalper (see D043).

Both checks use a chronological in-sample / out-of-sample (IS/OOS) split per
pair — never shuffled, because this is a time series and future bars must not
leak into the past.

1. FROZEN params (default): run the deployed `config.yaml` universal params on
   IS and OOS separately. Answers: do the params the bot actually trades hold
   up on unseen recent data? Fast (2 backtests per pair).

2. WALK-FORWARD (--reoptimize): grid-search the params on IS only, freeze the
   IS-best, then evaluate that frozen set on OOS. Answers: did the parameter
   *selection* overfit? Slow (full grid per pair on the IS slice).

Retention = OOS_PF / IS_PF. A value near/above 1.0 means the edge generalises;
a sharp drop (<~0.6) signals overfitting to the in-sample window.

Usage:
    python backtest/validate_oos.py                 # frozen params, 70/30 split
    python backtest/validate_oos.py --split 0.65    # custom IS fraction
    python backtest/validate_oos.py --reoptimize    # walk-forward (slow)
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path

import pandas as pd
from backtesting import Backtest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.download_data import LULL_PAIRS, load_lull_data
from backtest.lull_engine import DailyLullStrategy, prepare_lull_data, run_lull_backtest
from backtest.optimize_lull import (
    ADX_MAX_THRESHOLD,
    RANGE_ATR_MAX,
    RANGE_ATR_MIN,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    SL_ATR_MULT,
)
from drift.config import load_config

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
CASH: float = 500.0
COMMISSION: float = 0.00007

# Live pairs only — USDJPY is excluded from production (D029/D043).
LIVE_PAIRS = ["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"]

PARAM_KEYS = (
    "sl_atr_mult",
    "adx_max_threshold",
    "rsi_oversold",
    "rsi_overbought",
    "range_atr_min",
    "range_atr_max",
)


def _safe(val: object) -> float:
    try:
        f = float(val)  # type: ignore[arg-type]
        return f if math.isfinite(f) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _metrics(stats: pd.Series) -> dict:
    return {
        "return_pct": _safe(stats.get("Return [%]")),
        "pf": _safe(stats.get("Profit Factor")),
        "trades": _safe(stats.get("# Trades")),
        "win_rate": _safe(stats.get("Win Rate [%]")),
        "max_dd": _safe(stats.get("Max. Drawdown [%]")),
    }


def split_chrono(df: pd.DataFrame, is_frac: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a prepared DataFrame chronologically into (in_sample, out_of_sample)."""
    k = int(len(df) * is_frac)
    return df.iloc[:k], df.iloc[k:]


def frozen_params(config) -> dict:
    """The deployed universal params from config.yaml."""
    s = config.strategy
    return {
        "sl_atr_mult": s.sl_atr_mult,
        "adx_max_threshold": s.adx_max_threshold,
        "rsi_oversold": s.rsi_oversold,
        "rsi_overbought": s.rsi_overbought,
        "range_atr_min": s.range_atr_min,
        "range_atr_max": s.range_atr_max,
    }


def optimize_on(df: pd.DataFrame) -> dict | None:
    """Grid-search the params on *df* (the in-sample slice). Returns best params."""
    bt = Backtest(df, DailyLullStrategy, cash=CASH, commission=COMMISSION, exclusive_orders=True)
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
        logger.warning("optimize failed: %s", exc)
        return None
    s = stats._strategy
    return {k: getattr(s, k) for k in PARAM_KEYS}


def evaluate(df: pd.DataFrame, params: dict) -> dict:
    stats, _ = run_lull_backtest(df, cash=CASH, commission=COMMISSION, **params)
    return _metrics(stats)


def _fmt(m: dict) -> str:
    ret = f"{m['return_pct']:+6.2f}%" if math.isfinite(m["return_pct"]) else "   N/A"
    pf = f"{m['pf']:5.2f}" if math.isfinite(m["pf"]) else " N/A "
    tr = f"{int(m['trades']):4}" if math.isfinite(m["trades"]) else " N/A"
    wr = f"{m['win_rate']:5.1f}%" if math.isfinite(m["win_rate"]) else "  N/A"
    return f"ret={ret}  PF={pf}  trades={tr}  WR={wr}"


def run(pairs: list[str], is_frac: float, reoptimize: bool) -> None:
    config = load_config()
    mode = "WALK-FORWARD (re-optimize on IS)" if reoptimize else "FROZEN config params"
    print("=" * 78)
    print(f"  OUT-OF-SAMPLE VALIDATION — Daily Lull Scalper  [{mode}]")
    print(f"  Split per pair: IS = first {is_frac:.0%} | OOS = last {1 - is_frac:.0%}")
    print("=" * 78)

    rows: list[dict] = []
    for pair in pairs:
        df_h4, df_m15 = load_lull_data(pair, DATA_DIR)
        df = prepare_lull_data(df_h4, df_m15)
        is_df, oos_df = split_chrono(df, is_frac)
        is_start, is_end = str(df.index[0])[:10], str(is_df.index[-1])[:10]
        oos_start, oos_end = str(oos_df.index[0])[:10], str(df.index[-1])[:10]

        if reoptimize:
            params = optimize_on(is_df)
            if params is None:
                print(f"\n{pair}: optimization failed — skipped")
                continue
        else:
            params = frozen_params(config)

        is_m = evaluate(is_df, params)
        oos_m = evaluate(oos_df, params)
        retention = (
            oos_m["pf"] / is_m["pf"]
            if math.isfinite(is_m["pf"]) and is_m["pf"] > 0 and math.isfinite(oos_m["pf"])
            else float("nan")
        )

        print(f"\n{'-' * 78}\n{pair}   (IS {is_start}..{is_end}  |  OOS {oos_start}..{oos_end})")
        if reoptimize:
            pstr = "  ".join(f"{k}={params[k]}" for k in PARAM_KEYS)
            print(f"  IS-best params: {pstr}")
        print(f"  IN-SAMPLE   : {_fmt(is_m)}")
        print(f"  OUT-SAMPLE  : {_fmt(oos_m)}")
        ret_str = f"{retention:.2f}" if math.isfinite(retention) else "N/A"
        print(f"  Retention (OOS PF / IS PF): {ret_str}")
        rows.append({"pair": pair, "is": is_m, "oos": oos_m, "retention": retention})

    _summary(rows)


def _summary(rows: list[dict]) -> None:
    print(f"\n{'=' * 78}\n  SUMMARY\n{'=' * 78}")
    header = (
        f"{'Pair':<8} | {'IS PF':>6} | {'OOS PF':>6} | "
        f"{'IS ret':>8} | {'OOS ret':>8} | {'Retent':>9}"
    )
    print(header)
    print("-" * len(header))
    rets = []
    for r in rows:
        isp = f"{r['is']['pf']:.2f}" if math.isfinite(r["is"]["pf"]) else " N/A"
        oosp = f"{r['oos']['pf']:.2f}" if math.isfinite(r["oos"]["pf"]) else " N/A"
        isr = f"{r['is']['return_pct']:+.2f}%" if math.isfinite(r["is"]["return_pct"]) else "  N/A"
        oosr = (
            f"{r['oos']['return_pct']:+.2f}%" if math.isfinite(r["oos"]["return_pct"]) else "  N/A"
        )
        ret = f"{r['retention']:.2f}" if math.isfinite(r["retention"]) else " N/A"
        if math.isfinite(r["retention"]):
            rets.append(r["retention"])
        print(f"{r['pair']:<8} | {isp:>6} | {oosp:>6} | {isr:>8} | {oosr:>8} | {ret:>9}")
    print("-" * len(header))

    # Portfolio OOS aggregate (equal $500 per pair)
    oos_pnl = sum(
        CASH * r["oos"]["return_pct"] / 100.0 for r in rows if math.isfinite(r["oos"]["return_pct"])
    )
    n = len([r for r in rows if math.isfinite(r["oos"]["return_pct"])])
    oos_port = (oos_pnl / (CASH * n) * 100.0) if n else float("nan")
    oos_pf_avg = sum(rets) / len(rets) if rets else float("nan")
    profitable_oos = sum(
        1 for r in rows if math.isfinite(r["oos"]["return_pct"]) and r["oos"]["return_pct"] > 0
    )

    print(f"\n  OOS portfolio ({n} pairs): {oos_port:+.2f}%   avg OOS PF: {oos_pf_avg:.2f}")
    print(f"  Pairs profitable OOS: {profitable_oos}/{len(rows)}")
    med_ret = sorted(rets)[len(rets) // 2] if rets else float("nan")
    if math.isfinite(med_ret):
        if med_ret >= 0.7 and profitable_oos >= len(rows) - 1:
            verdict = "HOLDS — edge generalises out-of-sample"
        elif med_ret >= 0.4:
            verdict = "PARTIAL — some decay; review per-pair before live"
        else:
            verdict = "WEAK — large OOS decay, likely overfit"
        print(f"  Median retention: {med_ret:.2f}  →  {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Out-of-sample validation for the Daily Lull Scalper"
    )
    parser.add_argument(
        "--split", type=float, default=0.70, help="In-sample fraction (default 0.70)"
    )
    parser.add_argument(
        "--reoptimize",
        action="store_true",
        help="Walk-forward: grid-search params on IS, test on OOS (slow)",
    )
    parser.add_argument(
        "--all-pairs",
        action="store_true",
        help="Include USDJPY (default: 5 live pairs only)",
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1252
    if not 0.4 <= args.split <= 0.9:
        parser.error("--split must be between 0.4 and 0.9")
    pairs = LULL_PAIRS if args.all_pairs else LIVE_PAIRS
    run(pairs, args.split, args.reoptimize)


if __name__ == "__main__":
    main()
