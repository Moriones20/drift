from __future__ import annotations

import logging
import math

from drift.config import RiskConfig

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


def check_all_risk(
    balance: float,
    peak_balance: float,
    open_trades: list[dict],
    new_pair: str,
    new_direction: str,
    config: RiskConfig,
) -> tuple[bool, str]:
    ok, reason = check_drawdown(balance, peak_balance, config.max_drawdown_percent)
    if not ok:
        return False, reason

    ok, reason = check_max_trades(open_trades, config.max_open_trades)
    if not ok:
        return False, reason

    ok, reason = check_correlation(
        open_trades, new_pair, new_direction, config.max_same_currency_direction
    )
    if not ok:
        return False, reason

    return True, ""
