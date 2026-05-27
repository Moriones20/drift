from __future__ import annotations

import logging

import MetaTrader5 as mt5

from drift.config import RiskConfig
from drift.executor import close_trade, modify_sl
from drift.indicators import compute_atr
from drift.mt5_client import get_candles

logger = logging.getLogger(__name__)

_H4_CANDLES_FOR_ATR = 100


def update_trailing(
    position: dict,
    current_price: float,
    atr_value: float,
    atr_multiplier: float,
) -> float | None:
    direction = position["direction"].lower()
    current_sl = position["sl"]
    distance = atr_value * atr_multiplier

    if direction == "buy":
        new_sl = current_price - distance
        if new_sl > current_sl:
            logger.debug(
                "update_trailing: ticket=%d BUY new_sl=%.5f > current_sl=%.5f — update",
                position["ticket"],
                new_sl,
                current_sl,
            )
            return new_sl
        logger.debug(
            "update_trailing: ticket=%d BUY new_sl=%.5f <= current_sl=%.5f — no update",
            position["ticket"],
            new_sl,
            current_sl,
        )
        return None

    if direction == "sell":
        new_sl = current_price + distance
        if new_sl < current_sl:
            logger.debug(
                "update_trailing: ticket=%d SELL new_sl=%.5f < current_sl=%.5f — update",
                position["ticket"],
                new_sl,
                current_sl,
            )
            return new_sl
        logger.debug(
            "update_trailing: ticket=%d SELL new_sl=%.5f >= current_sl=%.5f — no update",
            position["ticket"],
            new_sl,
            current_sl,
        )
        return None

    logger.warning(
        "update_trailing: ticket=%d unknown direction '%s'",
        position["ticket"],
        position["direction"],
    )
    return None


def check_tp(position: dict, current_price: float) -> bool:
    direction = position["direction"].lower()
    tp = position["tp"]

    if tp == 0.0:
        return False

    if direction == "buy":
        return current_price >= tp
    if direction == "sell":
        return current_price <= tp

    logger.warning(
        "check_tp: ticket=%d unknown direction '%s'",
        position["ticket"],
        position["direction"],
    )
    return False


def _get_current_price(pair: str, direction: str) -> float | None:
    tick = mt5.symbol_info_tick(pair)
    if tick is None:
        logger.error(
            "_get_current_price: cannot get tick for %s, last_error=%s",
            pair,
            mt5.last_error(),
        )
        return None
    return tick.bid if direction.lower() == "buy" else tick.ask


def _get_h4_atr(pair: str, atr_period: int = 14) -> float | None:
    try:
        df = get_candles(pair, "H4", count=_H4_CANDLES_FOR_ATR)
    except RuntimeError as exc:
        logger.error("_get_h4_atr: failed to fetch H4 candles for %s — %s", pair, exc)
        return None

    try:
        atr_series = compute_atr(df, period=atr_period)
    except ValueError as exc:
        logger.error("_get_h4_atr: ATR computation failed for %s — %s", pair, exc)
        return None

    latest = atr_series.dropna()
    if latest.empty:
        logger.error("_get_h4_atr: ATR series is all NaN for %s", pair)
        return None

    return float(latest.iloc[-1])


def process_open_trades(positions: list[dict], config: RiskConfig) -> list[dict]:
    actions: list[dict] = []

    for position in positions:
        ticket = position["ticket"]
        pair = position["pair"]
        direction = position["direction"]

        current_price = _get_current_price(pair, direction)
        if current_price is None:
            logger.warning("process_open_trades: skipping ticket=%d — no price data", ticket)
            continue

        atr_value = _get_h4_atr(pair)
        if atr_value is None:
            logger.warning("process_open_trades: skipping ticket=%d — no ATR data", ticket)
            continue

        if check_tp(position, current_price):
            logger.info(
                "process_open_trades: ticket=%d %s TP hit at price=%.5f tp=%.5f — closing",
                ticket,
                pair,
                current_price,
                position["tp"],
            )
            success = close_trade(
                ticket=ticket,
                pair=pair,
                lot_size=position["volume"],
                direction=direction,
            )
            actions.append(
                {
                    "ticket": ticket,
                    "action": "tp_closed",
                    "detail": (
                        f"price={current_price:.5f} tp={position['tp']:.5f} success={success}"
                    ),
                }
            )
            continue

        new_sl = update_trailing(
            position=position,
            current_price=current_price,
            atr_value=atr_value,
            atr_multiplier=config.trailing_stop_atr_multiplier,
        )

        if new_sl is not None:
            logger.info(
                "process_open_trades: ticket=%d %s updating SL %.5f → %.5f",
                ticket,
                pair,
                position["sl"],
                new_sl,
            )
            success = modify_sl(ticket=ticket, pair=pair, new_sl=new_sl)
            actions.append(
                {
                    "ticket": ticket,
                    "action": "sl_updated",
                    "detail": (
                        f"old_sl={position['sl']:.5f} new_sl={new_sl:.5f} "
                        f"atr={atr_value:.5f} success={success}"
                    ),
                }
            )
        else:
            logger.debug(
                "process_open_trades: ticket=%d %s no SL update needed price=%.5f sl=%.5f atr=%.5f",
                ticket,
                pair,
                current_price,
                position["sl"],
                atr_value,
            )

    return actions
