"""Compare the Daily Lull backtest WITH vs WITHOUT the 00:00 rollover bar.

The entry-hour analysis showed ~68% of profit (88% win rate) comes from the
single 00:00 server bar — the broker rollover, which live cannot execute
cleanly. This script re-runs each live pair dropping only the 00:00-bar
entries, to see whether a realizable edge survives underneath the artifact.

Usage:  python backtest/analyze_exclude_rollover.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtesting import Backtest

from backtest.download_data import load_lull_data
from backtest.lull_engine import DailyLullStrategy, prepare_lull_data
from drift.config import load_config

DATA_DIR = Path(__file__).parent / "data"
LIVE_PAIRS = ["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"]
CASH = 500.0
COMMISSION = 0.00007


def _run(df, cfg, skip: bool):
    bt = Backtest(df, DailyLullStrategy, cash=CASH, commission=COMMISSION, exclusive_orders=True)
    return bt.run(
        skip_rollover_bar=skip,
        sl_atr_mult=cfg.sl_atr_mult,
        adx_max_threshold=cfg.adx_max_threshold,
        rsi_oversold=cfg.rsi_oversold,
        rsi_overbought=cfg.rsi_overbought,
        range_atr_min=cfg.range_atr_min,
        range_atr_max=cfg.range_atr_max,
    )


def _pf(s) -> str:
    v = s.get("Profit Factor", float("nan"))
    return f"{v:.2f}" if isinstance(v, (int, float)) and math.isfinite(v) else " inf"


def main() -> None:
    cfg = load_config().strategy
    print(
        f"\n{'Pair':<8} | {'mode':<9} | {'Ret%':>7} | {'PF':>5} | {'Win%':>6} | {'Trades':>6} | {'PnL$':>8}"  # noqa: E501
    )
    print("-" * 70)

    tot = {"full": [0.0, 0], "skip": [0.0, 0]}
    for sym in LIVE_PAIRS:
        h4, m15 = load_lull_data(sym, DATA_DIR)
        df = prepare_lull_data(h4, m15)
        for label, skip in [("full", False), ("no-00:00", True)]:
            s = _run(df, cfg, skip)
            ret = float(s.get("Return [%]", float("nan")))
            wr = float(s.get("Win Rate [%]", float("nan")))
            n = int(s.get("# Trades", 0))
            pnl = CASH * ret / 100.0
            print(
                f"{sym:<8} | {label:<9} | {ret:>+6.2f}% | {_pf(s):>5} | {wr:>5.1f}% | {n:>6} | {pnl:>+8.2f}"  # noqa: E501
            )
            key = "full" if not skip else "skip"
            tot[key][0] += pnl
            tot[key][1] += n
        print("-" * 70)

    cap = CASH * len(LIVE_PAIRS)
    print(f"\nPORTFOLIO (5 pares, ${cap:.0f} desplegado):")
    print(
        f"  FULL     : ${tot['full'][0]:+8.2f}  ({tot['full'][0] / cap * 100:+.2f}%)  "
        f"{tot['full'][1]} trades"
    )
    print(
        f"  NO 00:00 : ${tot['skip'][0]:+8.2f}  ({tot['skip'][0] / cap * 100:+.2f}%)  "
        f"{tot['skip'][1]} trades"
    )
    lost = tot["full"][0] - tot["skip"][0]
    print(
        f"\n  -> Quitar la vela 00:00 elimina ${lost:+.2f} "
        f"({lost / tot['full'][0] * 100:.0f}% del PnL) y "
        f"{tot['full'][1] - tot['skip'][1]} trades."
    )


if __name__ == "__main__":
    main()
