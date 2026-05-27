from __future__ import annotations

import logging
from datetime import datetime, timezone

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


def open_trade(
    pair: str,
    direction: str,
    lot_size: float,
    stop_loss: float,
    take_profit: float,
    magic: int,
) -> int | None:
    tick = mt5.symbol_info_tick(pair)
    if tick is None:
        logger.error("open_trade: cannot get tick for %s", pair)
        return None

    if direction.lower() == "buy":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pair,
        "volume": lot_size,
        "type": order_type,
        "price": price,
        "sl": stop_loss,
        "tp": take_profit,
        "deviation": 20,
        "magic": magic,
        "comment": "Drift",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result is None:
        logger.error(
            "open_trade: order_send returned None for %s %s, last_error=%s",
            direction,
            pair,
            mt5.last_error(),
        )
        return None

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(
            "open_trade: failed %s %s lot=%.2f sl=%.5f tp=%.5f retcode=%d comment=%s",
            direction,
            pair,
            lot_size,
            stop_loss,
            take_profit,
            result.retcode,
            result.comment,
        )
        return None

    logger.info(
        "open_trade: opened %s %s ticket=%d lot=%.2f price=%.5f sl=%.5f tp=%.5f",
        direction,
        pair,
        result.order,
        lot_size,
        price,
        stop_loss,
        take_profit,
    )
    return result.order


def close_trade(ticket: int, pair: str, lot_size: float, direction: str) -> bool:
    tick = mt5.symbol_info_tick(pair)
    if tick is None:
        logger.error("close_trade: cannot get tick for %s", pair)
        return False

    if direction.lower() == "buy":
        close_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        close_type = mt5.ORDER_TYPE_BUY
        price = tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pair,
        "volume": lot_size,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": 0,
        "comment": "Drift",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result is None:
        logger.error(
            "close_trade: order_send returned None for ticket=%d %s, last_error=%s",
            ticket,
            pair,
            mt5.last_error(),
        )
        return False

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(
            "close_trade: failed ticket=%d %s retcode=%d comment=%s",
            ticket,
            pair,
            result.retcode,
            result.comment,
        )
        return False

    logger.info(
        "close_trade: closed ticket=%d %s lot=%.2f price=%.5f",
        ticket,
        pair,
        lot_size,
        price,
    )
    return True


def modify_sl(ticket: int, pair: str, new_sl: float) -> bool:
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        logger.error("modify_sl: position not found for ticket=%d", ticket)
        return False

    position = positions[0]

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": pair,
        "position": ticket,
        "sl": new_sl,
        "tp": position.tp,
    }

    result = mt5.order_send(request)

    if result is None:
        logger.error(
            "modify_sl: order_send returned None for ticket=%d, last_error=%s",
            ticket,
            mt5.last_error(),
        )
        return False

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(
            "modify_sl: failed ticket=%d new_sl=%.5f retcode=%d comment=%s",
            ticket,
            new_sl,
            result.retcode,
            result.comment,
        )
        return False

    logger.info("modify_sl: ticket=%d sl updated to %.5f", ticket, new_sl)
    return True


def get_open_positions(magic: int) -> list[dict]:
    all_positions = mt5.positions_get()
    if all_positions is None:
        logger.warning(
            "get_open_positions: positions_get returned None, last_error=%s",
            mt5.last_error(),
        )
        return []

    result = []
    for pos in all_positions:
        if pos.magic != magic:
            continue

        direction = "buy" if pos.type == 0 else "sell"
        time_open = datetime.fromtimestamp(pos.time, tz=timezone.utc)

        result.append(
            {
                "ticket": pos.ticket,
                "pair": pos.symbol,
                "direction": direction,
                "volume": pos.volume,
                "price_open": pos.price_open,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit,
                "time_open": time_open,
            }
        )

    return result
