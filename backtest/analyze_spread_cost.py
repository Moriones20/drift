"""Re-price the Daily Lull backtest under a realistic spread model (D046 follow-up).

The standard backtest fills at the bar Open (MT5 candles are BID) and only
charges a flat commission — it never pays the bid/ask spread, and in particular
never pays the spread *blowout* during the broker's 00:00 server rollover, which
is exactly where ~92% of the idealized profit sits (D045). A live market BUY
fills at the ASK (= bid + full spread); a SELL exits at the ASK. So the cost the
backtest omits is roughly ONE full spread per trade, taken at the entry-fill time
for longs and the exit-fill time for shorts — and that spread is several times
wider for fills inside the rollover window.

The baseline backtest already charges a flat commission (0.00007/side ≈ 1.5 pips
round-trip) that reasonably stands in for *normal* trading costs. What it does
NOT capture is the rollover spread blowout. So this script charges only the
EXCESS spread over normal conditions, and only for fills inside the rollover
window — isolating exactly the cost D046 is about. Net = baseline gross minus
that excess. (A fully pessimistic "pay the whole spread on every trade" view is
also printed as a lower bound.)

Spreads below are full bid/ask in pips (ICMarkets raw demo): a "normal" value and
an inflated "rollover" value for fills at 00:00-00:29 server time. The rollover
figures are anchored to the gaps actually observed live on 2026-06-04
(EURJPY ~10, GBPJPY ~13, EURGBP ~5 pips).

Usage:  python backtest/analyze_spread_cost.py
"""

from __future__ import annotations

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
COMMISSION = 0.00007  # same flat commission as the baseline backtest

# pip size per pair
PIP = {
    "AUDNZD": 0.0001,
    "EURCHF": 0.0001,
    "EURGBP": 0.0001,
    "EURJPY": 0.01,
    "GBPJPY": 0.01,
}

# Full bid/ask spread in pips: (normal, rollover-window).
SPREAD_PIPS = {
    "AUDNZD": (1.8, 8.0),
    "EURCHF": (1.5, 5.0),
    "EURGBP": (1.2, 5.0),
    "EURJPY": (1.5, 11.0),
    "GBPJPY": (2.2, 14.0),
}


def _is_rollover_fill(ts) -> bool:
    """True if a fill at *ts* (server time) lands in the rollover blowout window."""
    return int(ts.hour) == 0 and int(ts.minute) < 30


def _excess_price(pair: str, ts) -> float:
    """Rollover excess spread (over normal) in price units for a fill at *ts*.

    Zero outside the rollover window — normal-condition spread is assumed already
    covered by the baseline commission.
    """
    normal, rollover = SPREAD_PIPS[pair]
    if not _is_rollover_fill(ts):
        return 0.0
    return (rollover - normal) * PIP[pair]


def _full_price(pair: str, ts) -> float:
    """Full spread in price units for a fill at *ts* (pessimistic lower bound)."""
    normal, rollover = SPREAD_PIPS[pair]
    pips = rollover if _is_rollover_fill(ts) else normal
    return pips * PIP[pair]


def _run(df, cfg):
    bt = Backtest(df, DailyLullStrategy, cash=CASH, commission=COMMISSION, exclusive_orders=True)
    stats = bt.run(
        sl_atr_mult=cfg.sl_atr_mult,
        adx_max_threshold=cfg.adx_max_threshold,
        rsi_oversold=cfg.rsi_oversold,
        rsi_overbought=cfg.rsi_overbought,
        range_atr_min=cfg.range_atr_min,
        range_atr_max=cfg.range_atr_max,
    )
    return stats


def main() -> None:
    cfg = load_config().strategy
    print(
        f"\n{'Pair':<8} | {'Gross$':>8} | {'Excess$':>8} | {'Net$':>8} | "
        f"{'NetRet%':>7} | {'Trades':>6} | {'RollTr':>6}"
    )
    print("-" * 72)

    tot_gross = tot_excess = tot_full = 0.0
    tot_trades = tot_roll = 0
    roll_gross = roll_net = 0.0

    for sym in LIVE_PAIRS:
        h4, m15 = load_lull_data(sym, DATA_DIR)
        df = prepare_lull_data(h4, m15)
        trades = _run(df, cfg)._trades

        gross = excess = full = 0.0
        n = n_roll = 0
        for _, t in trades.iterrows():
            size = abs(float(t["Size"]))
            pnl = float(t["PnL"])
            fill_ts = t["EntryTime"] if float(t["Size"]) > 0 else t["ExitTime"]
            ex = _excess_price(sym, fill_ts) * size
            gross += pnl
            excess += ex
            full += _full_price(sym, fill_ts) * size
            n += 1
            if _is_rollover_fill(t["EntryTime"]):
                n_roll += 1
                roll_gross += pnl
                roll_net += pnl - ex

        net = gross - excess
        print(
            f"{sym:<8} | {gross:>+8.2f} | {excess:>8.2f} | {net:>+8.2f} | "
            f"{net / CASH * 100.0:>+6.2f}% | {n:>6} | {n_roll:>6}"
        )
        tot_gross += gross
        tot_excess += excess
        tot_full += full
        tot_trades += n
        tot_roll += n_roll

    cap = CASH * len(LIVE_PAIRS)
    tot_net = tot_gross - tot_excess
    tot_pessimistic = tot_gross - tot_full
    print("-" * 72)
    print(f"\nPORTFOLIO (5 pares, ${cap:.0f} desplegado):")
    print(
        f"  Gross (ideal, sin spread rollover): ${tot_gross:+8.2f}  ({tot_gross / cap * 100:+.2f}%)"
    )
    print(
        f"  Modelo CENTRAL (exceso rollover)  : ${tot_net:+8.2f}  ({tot_net / cap * 100:+.2f}%)  "
        f"-> sobrevive {tot_net / tot_gross * 100:.0f}%"
    )
    print(
        f"  Cota PESIMISTA (spread completo)  : ${tot_pessimistic:+8.2f}  "
        f"({tot_pessimistic / cap * 100:+.2f}%)  "
        f"-> sobrevive {tot_pessimistic / tot_gross * 100:.0f}%"
    )
    print(
        f"\n  Trades: {tot_trades} | en ventana rollover (00:00-00:29): "
        f"{tot_roll} ({tot_roll / tot_trades * 100:.0f}%)"
    )
    print(
        f"  PnL rollover (modelo central): bruto ${roll_gross:+.2f} -> neto ${roll_net:+.2f}  "
        f"(el exceso se come ${roll_gross - roll_net:.2f})"
    )


if __name__ == "__main__":
    main()
