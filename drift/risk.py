from __future__ import annotations

import logging
import math

from drift.config import RiskGlobalConfig, StrategyRiskConfig

logger = logging.getLogger(__name__)

COMMISSION_PER_LOT = 7.0


def calculate_position_size(
    balance: float,
    risk_percent: float,
    stop_loss_pips: float,
    pip_value: float,
) -> float:
    if stop_loss_pips <= 0 or pip_value <= 0:
        logger.warning("Invalid stop_loss_pips=%.5f or pip_value=%.5f", stop_loss_pips, pip_value)
        return 0.0

    risk_amount = balance * (risk_percent / 100)

    # Solve algebraically to avoid circular commission dependency:
    # lot = risk_amount / (sl_pips * pip_value + COMMISSION_PER_LOT)
    denominator = stop_loss_pips * pip_value + COMMISSION_PER_LOT
    lot_size = risk_amount / denominator
    lot_size = math.floor(lot_size * 100) / 100

    if lot_size < 0.01:
        logger.warning(
            "Calculated lot size %.4f is below minimum 0.01 for balance=%.2f, "
            "risk=%.1f%%, sl_pips=%.1f, pip_value=%.5f",
            lot_size,
            balance,
            risk_percent,
            stop_loss_pips,
            pip_value,
        )
        return 0.0

    return lot_size


def check_max_trades(open_trades: list[dict], max_trades: int) -> tuple[bool, str]:
    count = len(open_trades)
    if count >= max_trades:
        return False, f"max trades reached ({count}/{max_trades})"
    return True, ""


def check_correlation(
    open_trades: list[dict],
    new_pair: str,
    new_direction: str,
    max_same: int,
) -> tuple[bool, str]:
    new_base = new_pair[:3].upper()
    new_quote = new_pair[3:].upper()
    new_dir = new_direction.lower()

    # Currencies being sold by the new trade
    selling_new: set[str] = set()
    buying_new: set[str] = set()
    if new_dir == "buy":
        buying_new.add(new_base)
        selling_new.add(new_quote)
    else:
        selling_new.add(new_base)
        buying_new.add(new_quote)

    for currency in selling_new:
        count = _count_currency_exposure(open_trades, currency, "sell")
        if count >= max_same:
            return False, f"correlation limit: {count} trades already selling {currency}"

    for currency in buying_new:
        count = _count_currency_exposure(open_trades, currency, "buy")
        if count >= max_same:
            return False, f"correlation limit: {count} trades already buying {currency}"

    return True, ""


def _count_currency_exposure(open_trades: list[dict], currency: str, side: str) -> int:
    count = 0
    for trade in open_trades:
        pair = trade["pair"].upper()
        direction = trade["direction"].lower()
        base = pair[:3]
        quote = pair[3:]
        if side == "buy":
            if (direction == "buy" and base == currency) or (
                direction == "sell" and quote == currency
            ):
                count += 1
        elif side == "sell":
            if (direction == "sell" and base == currency) or (
                direction == "buy" and quote == currency
            ):
                count += 1
    return count


def check_drawdown(
    current_balance: float,
    peak_balance: float,
    max_drawdown_percent: float,
) -> tuple[bool, str]:
    if peak_balance <= 0:
        logger.warning("peak_balance=%.2f is invalid", peak_balance)
        return True, ""

    drawdown = (peak_balance - current_balance) / peak_balance * 100

    if drawdown >= max_drawdown_percent:
        return False, f"drawdown {drawdown:.1f}% exceeds limit {max_drawdown_percent:.0f}%"

    return True, ""


# ---------------------------------------------------------------------------
# Two-level notional risk engine (D053)
#
# On a single MT5 account, each strategy is allocated a NOTIONAL slice of the
# account balance.  The allocation does not reserve or move real money; it only
# changes the number each strategy sizes against:
#
#     capital_asignado(strategy) = account_balance * (allocation_pct / 100)
#     risk_usd(trade)            = capital_asignado * (percent_per_trade / 100)
#
# Limits live at two levels (see D053):
#   - max_open_trades:            per-strategy cap AND global account cap.
#   - max_drawdown_percent:       per-strategy brake (pauses that strategy) AND
#                                 the existing global brake (kill switch, all).
#   - max_same_currency_direction (correlation): GLOBAL only — correlation is an
#                                 account-wide risk regardless of who opened it.
# ---------------------------------------------------------------------------


def calculate_position_size_allocated(
    balance: float,
    allocation_pct: float,
    percent_per_trade: float,
    stop_loss_pips: float,
    pip_value: float,
) -> float:
    """Size a position against a strategy's NOTIONAL capital allocation (D053).

    The strategy risks ``percent_per_trade`` of its allocated capital, where the
    allocated capital is ``balance * allocation_pct / 100`` (the notional slice
    of the single account, D053).  This is equivalent to risking
    ``allocation_pct/100 * percent_per_trade`` percent of the full account
    balance, so the pip/commission math of :func:`calculate_position_size` is
    reused directly (no duplication) by folding the allocation into the effective
    risk percent.

    Args:
        balance: Full account balance in account currency.
        allocation_pct: Strategy's notional allocation of the account (0-100).
        percent_per_trade: Risk per trade as a percent of the allocated capital.
        stop_loss_pips: Stop-loss distance in pips.
        pip_value: Value of one pip per lot in account currency.

    Returns:
        Lot size rounded down to 0.01, or 0.0 if below the 0.01 minimum or on
        invalid inputs.
    """
    if allocation_pct <= 0:
        logger.warning("allocation_pct=%.4f is non-positive; no capital allocated", allocation_pct)
        return 0.0

    effective_risk_percent = (allocation_pct / 100) * percent_per_trade
    return calculate_position_size(balance, effective_risk_percent, stop_loss_pips, pip_value)


def check_strategy_risk(
    strategy_open_trades: list[dict],
    account_open_trades: list[dict],
    new_pair: str,
    new_direction: str,
    strategy_risk: StrategyRiskConfig,
    risk_global: RiskGlobalConfig,
) -> tuple[bool, str]:
    """Gate a new entry against per-strategy and global limits (D053).

    Checks, in order:
      (a) per-strategy ``max_open_trades`` over the strategy's own positions,
      (b) global ``max_open_trades`` over ALL account positions,
      (c) global ``max_same_currency_direction`` correlation over ALL account
          positions (correlation is account-wide, never per-strategy — D053).

    Drawdown brakes are NOT checked here; the global brake is
    :func:`check_drawdown` (kill switch) and the per-strategy brake is
    :func:`check_strategy_drawdown`, both evaluated by the engine separately.

    Args:
        strategy_open_trades: Open positions belonging to this strategy.
        account_open_trades: All open positions on the account.
        new_pair: Symbol of the prospective entry (e.g. "EURUSD").
        new_direction: "buy" or "sell".
        strategy_risk: This strategy's risk budget.
        risk_global: Account-wide risk limits.

    Returns:
        ``(ok, reason)`` — ``reason`` is empty when ``ok`` is True.
    """
    ok, reason = check_max_trades(strategy_open_trades, strategy_risk.max_open_trades)
    if not ok:
        return False, f"strategy {reason}"

    ok, reason = check_max_trades(account_open_trades, risk_global.max_open_trades)
    if not ok:
        return False, f"account {reason}"

    ok, reason = check_correlation(
        account_open_trades, new_pair, new_direction, risk_global.max_same_currency_direction
    )
    if not ok:
        return False, reason

    return True, ""


def seed_baseline_capital(
    balance: float,
    allocation_pct: float,
    realized_lifetime: float,
) -> float:
    """Compute the persisted notional baseline for a strategy's equity (D062).

    ``baseline_capital = balance * allocation_pct/100 - realized_lifetime``.

    The MT5 ``balance`` already includes realized P&L, so the notional slice
    ``balance * allocation_pct/100`` already carries the strategy's past realized
    P&L.  Subtracting ``realized_lifetime`` here removes it from the baseline, so
    that :func:`strategy_equity` (which adds ``realized`` back in) does NOT count
    realized P&L twice.  The result is that at seed time the equity starts exactly
    at the allocated slice and forward realized P&L is counted once.  Seeded once
    and persisted in ``strategy_state.baseline_capital``.

    Args:
        balance: Full account balance at seed time (MT5 balance, realized).
        allocation_pct: Strategy's notional allocation of the account (0-100).
        realized_lifetime: Strategy's cumulative realized P&L at seed time.

    Returns:
        The notional baseline capital in account currency.
    """
    return balance * allocation_pct / 100.0 - realized_lifetime


def strategy_equity(
    allocated_baseline: float,
    realized_pnl: float,
    floating_pnl: float,
) -> float:
    """Compute a strategy's individual equity curve value (D053).

    ``equity = allocated_baseline + realized_pnl + floating_pnl``, where the
    baseline is the strategy's notional capital allocation, ``realized_pnl`` is
    its cumulative closed P&L (from the DB) and ``floating_pnl`` is the unrealized
    P&L of its currently open positions (by its magic number).  This is the curve
    the per-strategy drawdown brake runs on.

    Args:
        allocated_baseline: Strategy's notional capital allocation baseline.
        realized_pnl: Cumulative realized P&L of the strategy.
        floating_pnl: Unrealized P&L of the strategy's open positions.

    Returns:
        The strategy's current equity in account currency.
    """
    return allocated_baseline + realized_pnl + floating_pnl


def strategy_drawdown(equity: float, peak_equity: float) -> float:
    """Per-strategy drawdown as a fraction of peak equity (D053).

    ``drawdown = (peak_equity - equity) / peak_equity``.  Returns 0.0 when the
    peak is non-positive (no meaningful curve yet) to mirror the guard in
    :func:`check_drawdown`.

    Args:
        equity: Current strategy equity (see :func:`strategy_equity`).
        peak_equity: Highest strategy equity observed so far.

    Returns:
        Drawdown as a fraction in [0, 1+] (0.0 when peak_equity <= 0).
    """
    if peak_equity <= 0:
        return 0.0
    return (peak_equity - equity) / peak_equity


def check_strategy_drawdown(
    equity: float,
    peak_equity: float,
    max_drawdown_percent: float,
) -> tuple[bool, str]:
    """Per-strategy drawdown brake (D053).

    Analogue of the global :func:`check_drawdown`, but on the strategy's own
    equity curve (:func:`strategy_equity`).  When tripped it pauses only this
    strategy (it keeps its open positions, protected by their server-side SL/TP;
    pause = hold, D053), unlike the global brake which is the account kill switch.

    Args:
        equity: Current strategy equity in account currency.
        peak_equity: Highest strategy equity observed so far.
        max_drawdown_percent: Per-strategy drawdown limit, in percent.

    Returns:
        ``(ok, reason)`` — ``ok`` is False once drawdown reaches the limit.
    """
    if peak_equity <= 0:
        logger.warning("strategy peak_equity=%.2f is invalid", peak_equity)
        return True, ""

    drawdown = strategy_drawdown(equity, peak_equity) * 100

    if drawdown >= max_drawdown_percent:
        return (
            False,
            f"strategy drawdown {drawdown:.1f}% exceeds limit {max_drawdown_percent:.0f}%",
        )

    return True, ""
