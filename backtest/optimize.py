"""Walk-forward parameter optimization for Drift strategy.

Optimizes sl_atr_mult and tp_ratio only. EMA/MACD periods are fixed by design (D005).
Anti-overfitting: 70/30 train-test split, in-sample vs out-of-sample comparison.

Usage:
    python backtest/optimize.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
from backtesting import Backtest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.download_data import generate_synthetic_data, load_data
from backtest.engine_meanrev_legacy import PAIRS, DriftBacktestStrategy, prepare_backtest_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SL_RANGE = [1.0, 1.25, 1.5, 1.75, 2.0]
TP_RANGE = [1.5, 2.0, 2.5, 3.0]
DEFAULT_SL = 1.5
DEFAULT_TP = 2.0
TRAIN_RATIO = 0.7
COMMISSION = 0.00007


def _extract_metrics(stats: pd.Series) -> dict:
    """Pull the metrics we care about from a backtesting.py stats Series."""

    def _f(key: str) -> float:
        val = stats.get(key, float("nan"))
        try:
            return float(val)
        except (TypeError, ValueError):
            return float("nan")

    return {
        "return_pct": _f("Return [%]"),
        "sharpe": _f("Sharpe Ratio"),
        "profit_factor": _f("Profit Factor"),
        "win_rate": _f("Win Rate [%]"),
        "max_drawdown": _f("Max. Drawdown [%]"),
        "trades": int(stats.get("# Trades", 0)),
    }


def _run_single(df: pd.DataFrame, sl: float, tp: float, cash: float) -> dict:
    bt = Backtest(
        df, DriftBacktestStrategy, cash=cash, commission=COMMISSION, exclusive_orders=True
    )
    stats = bt.run(sl_atr_mult=sl, tp_ratio=tp)
    return _extract_metrics(stats)


def optimize_pair(
    symbol: str,
    df_d1: pd.DataFrame,
    df_h4: pd.DataFrame,
    cash: float = 500,
) -> dict:
    """Run walk-forward optimization for a single pair. Returns results dict."""
    logger.info("Optimizing %s...", symbol)

    # Split raw data BEFORE computing indicators to avoid look-ahead bias.
    cutoff = int(len(df_h4) * TRAIN_RATIO)
    h4_cutoff_time = df_h4.index[cutoff]
    df_h4_train = df_h4.iloc[:cutoff]
    df_h4_test = df_h4.iloc[cutoff:]
    df_d1_train = df_d1[df_d1.index <= h4_cutoff_time]
    df_d1_test = df_d1[df_d1.index > h4_cutoff_time]

    df_train = prepare_backtest_data(df_d1_train, df_h4_train)
    df_test = prepare_backtest_data(df_d1_test, df_h4_test)
    logger.info(
        "%s split — train: %d bars, test: %d bars",
        symbol,
        len(df_train),
        len(df_test),
    )

    bt_train = Backtest(
        df_train, DriftBacktestStrategy, cash=cash, commission=COMMISSION, exclusive_orders=True
    )
    best_stats, _ = bt_train.optimize(
        sl_atr_mult=SL_RANGE,
        tp_ratio=TP_RANGE,
        maximize="Sharpe Ratio",
        return_heatmap=True,
    )

    best_sl = float(best_stats._strategy.sl_atr_mult)  # type: ignore[attr-defined]
    best_tp = float(best_stats._strategy.tp_ratio)  # type: ignore[attr-defined]
    logger.info("%s best params — SL=%.2f, TP=%.2f", symbol, best_sl, best_tp)

    in_sample = _extract_metrics(best_stats)
    out_of_sample = _run_single(df_test, best_sl, best_tp, cash)
    default_oos = _run_single(df_test, DEFAULT_SL, DEFAULT_TP, cash)

    is_sharpe = in_sample["sharpe"]
    oos_sharpe = out_of_sample["sharpe"]
    oos_pf = out_of_sample["profit_factor"]
    oos_ret = out_of_sample["return_pct"]
    is_ret = in_sample["return_pct"]

    overfitting_warning = (
        (
            not pd.isna(is_sharpe)
            and not pd.isna(oos_sharpe)
            and is_sharpe > 0
            and oos_sharpe < is_sharpe * 0.5
        )
        or (not pd.isna(oos_pf) and oos_pf < 1.0)
        or (not pd.isna(is_ret) and not pd.isna(oos_ret) and is_ret > 0 and oos_ret < 0)
    )

    return {
        "symbol": symbol,
        "best_sl_atr_mult": best_sl,
        "best_tp_ratio": best_tp,
        "in_sample": in_sample,
        "out_of_sample": out_of_sample,
        "overfitting_warning": overfitting_warning,
        "default_params_oos": default_oos,
    }


def run_optimization(pairs: list[str], data_dir: Path, cash: float = 500) -> dict[str, dict]:
    """Run optimization on all pairs. Returns {pair: results}."""
    results: dict[str, dict] = {}

    for symbol in pairs:
        try:
            path_d1 = data_dir / f"{symbol}_D1.csv"
            path_h4 = data_dir / f"{symbol}_H4.csv"

            if path_d1.exists() and path_h4.exists():
                df_d1, df_h4 = load_data(symbol, data_dir)
                logger.info("Loaded CSV data for %s", symbol)
            else:
                logger.warning("No CSV data for %s — using synthetic", symbol)
                df_d1, df_h4 = generate_synthetic_data(symbol)

            results[symbol] = optimize_pair(symbol, df_d1, df_h4, cash)
        except Exception:
            logger.exception("Optimization failed for %s", symbol)

    return results


def print_optimization_report(all_results: dict[str, dict]) -> None:
    """Print formatted optimization report with overfitting warnings."""

    def _pct(v: float) -> str:
        if pd.isna(v):
            return "     N/A"
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.1f}%"

    def _num(v: float, decimals: int = 2) -> str:
        if pd.isna(v):
            return "     N/A"
        return f"{v:.{decimals}f}"

    def _improvement(oos_ret: float, def_ret: float) -> float:
        if pd.isna(oos_ret) or pd.isna(def_ret):
            return float("nan")
        return oos_ret - def_ret

    print()
    print("=" * 48)
    print("    DRIFT PARAMETER OPTIMIZATION REPORT")
    print("=" * 48)
    sl_min, sl_max = min(SL_RANGE), max(SL_RANGE)
    tp_min, tp_max = min(TP_RANGE), max(TP_RANGE)
    train_pct = int(TRAIN_RATIO * 100)
    test_pct = int((1 - TRAIN_RATIO) * 100)
    print(f"Walk-forward: {train_pct}% train / {test_pct}% test")
    print(f"Parameters: sl_atr_mult [{sl_min}-{sl_max}], tp_ratio [{tp_min}-{tp_max}]")

    improved: list[dict] = []
    keep_default: list[dict] = []

    for symbol, r in all_results.items():
        best_sl = r["best_sl_atr_mult"]
        best_tp = r["best_tp_ratio"]
        is_ = r["in_sample"]
        oos = r["out_of_sample"]
        def_oos = r["default_params_oos"]
        warn = r["overfitting_warning"]

        imp = _improvement(oos["return_pct"], def_oos["return_pct"])

        print()
        print(f"=== {symbol} ===")
        print(f"Default params: SL={DEFAULT_SL}x ATR, TP={DEFAULT_TP}x SL")
        print(f"Best found:     SL={best_sl}x ATR, TP={best_tp}x SL")
        print()
        print(f"{'':24}{'In-Sample':>12}{'Out-of-Sample':>15}")
        print(f"{'Return:':24}{_pct(is_['return_pct']):>12}{_pct(oos['return_pct']):>15}")
        print(f"{'Sharpe:':24}{_num(is_['sharpe']):>12}{_num(oos['sharpe']):>15}")
        pf_is = _num(is_["profit_factor"])
        pf_oos = _num(oos["profit_factor"])
        print(f"{'Profit Factor:':24}{pf_is:>12}{pf_oos:>15}")
        print(f"{'Win Rate:':24}{_pct(is_['win_rate']):>12}{_pct(oos['win_rate']):>15}")
        print(f"{'Max Drawdown:':24}{_pct(is_['max_drawdown']):>12}{_pct(oos['max_drawdown']):>15}")
        print(f"{'Trades:':24}{is_['trades']:>12}{oos['trades']:>15}")
        print()

        if not pd.isna(imp):
            imp_str = f"{'+' if imp >= 0 else ''}{imp:.1f}%"
            direction = "better" if imp >= 0 else "worse"
            def_ret_str = _pct(def_oos["return_pct"])
            print(
                f"vs Default (OOS):        {def_ret_str:>8}   (optimized is {imp_str} {direction})"
            )
        else:
            print("vs Default (OOS):        N/A")

        risk_label = "HIGH [!]" if warn else "LOW [ok]"
        print(f"Overfitting risk:        {risk_label}")

        if not pd.isna(imp) and imp > 0 and not warn:
            improved.append({"symbol": symbol, "sl": best_sl, "tp": best_tp, "improvement": imp})
        elif not pd.isna(imp) and imp > 0 and warn:
            keep_default.append(
                {
                    "symbol": symbol,
                    "sl": DEFAULT_SL,
                    "tp": DEFAULT_TP,
                    "reason": "overfitting risk despite improvement",
                }
            )
        else:
            keep_default.append(
                {
                    "symbol": symbol,
                    "sl": DEFAULT_SL,
                    "tp": DEFAULT_TP,
                    "reason": "optimized OOS worse than default",
                }
            )

    print()
    print("=" * 48)
    print("=== SUMMARY ===")
    print()

    if improved:
        print("Pairs where optimization helps (OOS > default OOS):")
        for item in improved:
            pct_str = f"+{item['improvement']:.1f}%"
            print(f"  {item['symbol']}: SL={item['sl']}, TP={item['tp']} -> {pct_str} improvement")
    else:
        print("No pairs where optimization clearly helps without overfitting risk.")

    print()
    if keep_default:
        print("Pairs where default is better (keep defaults):")
        for item in keep_default:
            print(f"  {item['symbol']}: {item['reason']}")

    print()
    print("RECOMMENDED PARAMETERS:")
    for symbol, r in all_results.items():
        warn = r["overfitting_warning"]
        best_sl = r["best_sl_atr_mult"]
        best_tp = r["best_tp_ratio"]

        imp = _improvement(
            r["out_of_sample"]["return_pct"],
            r["default_params_oos"]["return_pct"],
        )

        if not pd.isna(imp) and imp > 0 and not warn:
            print(f"  {symbol}: sl_atr_mult={best_sl}, tp_ratio={best_tp}")
        else:
            note = " (default — overfitting risk)" if warn else " (default)"
            print(f"  {symbol}: sl_atr_mult={DEFAULT_SL}, tp_ratio={DEFAULT_TP}{note}")

    print()


if __name__ == "__main__":
    data_dir = Path(__file__).parent / "data"
    all_results = run_optimization(PAIRS, data_dir)
    print_optimization_report(all_results)
