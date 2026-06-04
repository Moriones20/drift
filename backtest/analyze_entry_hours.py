"""Analyze the entry-time distribution of the Daily Lull backtest.

Question (raised 2026-06-03): live has only ever fired entries on the 00:00
server-time bar (the broker rollover). Is the *backtest* edge also concentrated
there, or spread across the 23:00-01:59 entry window?

For each live pair, run the backtest with the config (universal) params, then
bucket every trade by the SIGNAL bar's server hour:minute. Backtesting.py fills
market orders at the next bar's open, so signal_bar = EntryTime - one M15 bar.
Report count, PnL and win-rate per bucket, and isolate the 00:00 rollover bar.

Usage:  python backtest/analyze_entry_hours.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from backtest.download_data import load_lull_data
from backtest.lull_engine import prepare_lull_data, run_lull_backtest
from drift.config import load_config

DATA_DIR = Path(__file__).parent / "data"
LIVE_PAIRS = ["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"]  # USDJPY dropped (D029)


def main() -> None:
    cfg = load_config().strategy
    frames: list[pd.DataFrame] = []

    for sym in LIVE_PAIRS:
        df_h4, df_m15 = load_lull_data(sym, DATA_DIR)
        df_bt = prepare_lull_data(df_h4, df_m15)
        stats, _ = run_lull_backtest(
            df_bt,
            sl_atr_mult=cfg.sl_atr_mult,
            adx_max_threshold=cfg.adx_max_threshold,
            rsi_oversold=cfg.rsi_oversold,
            rsi_overbought=cfg.rsi_overbought,
            range_atr_min=cfg.range_atr_min,
            range_atr_max=cfg.range_atr_max,
        )
        trades = stats._trades
        if trades is None or len(trades) == 0:
            continue
        t = trades[["EntryTime", "ExitTime", "PnL"]].copy()
        t["signal_time"] = pd.to_datetime(t["EntryTime"]) - pd.Timedelta(minutes=15)
        t["pair"] = sym
        frames.append(t)

    t = pd.concat(frames, ignore_index=True)
    total_n = len(t)
    total_pnl = t["PnL"].sum()

    t["bucket"] = t["signal_time"].dt.strftime("%H:%M")
    g = t.groupby("bucket").agg(
        n=("PnL", "size"),
        pnl=("PnL", "sum"),
        wins=("PnL", lambda s: int((s > 0).sum())),
    )
    g["win%"] = (g["wins"] / g["n"] * 100).round(1)
    g["pnl"] = g["pnl"].round(2)
    g["%trades"] = (g["n"] / total_n * 100).round(1)
    g["%pnl"] = (g["pnl"] / total_pnl * 100).round(1)

    print(f"\nTotal backtest trades: {total_n}  |  total PnL: {total_pnl:.2f}")
    print("\n=== Signal-bar distribution (server time HH:MM) ===")
    print(g.sort_index()[["n", "%trades", "pnl", "%pnl", "win%"]].to_string())

    roll = t[t["bucket"] == "00:00"]
    print("\n=== 00:00 server bar (the rollover) ===")
    print(
        f"  trades: {len(roll)} ({len(roll) / total_n * 100:.1f}% of all)  |  "
        f"PnL: {roll['PnL'].sum():.2f} ({roll['PnL'].sum() / total_pnl * 100:.1f}% of total)"
    )
    if len(roll):
        print("  per pair:")
        print(roll.groupby("pair")["PnL"].agg(["size", "sum"]).round(2).to_string())

    t["hour"] = t["signal_time"].dt.hour
    gh = t.groupby("hour").agg(n=("PnL", "size"), pnl=("PnL", "sum"))
    gh["pnl"] = gh["pnl"].round(2)
    print("\n=== By signal hour (server) ===")
    print(gh.to_string())


if __name__ == "__main__":
    main()
