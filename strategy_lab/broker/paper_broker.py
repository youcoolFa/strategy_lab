"""
記憶體內的模擬交易所。沒有任何網路呼叫,不需要 API key。

`tick(price)` 被呼叫、價格穿越限價單的價位時就會成交——buy 在價格
「低於或等於」限價時成交,sell 在價格「高於或等於」限價時成交。
狀態用的字眼(open/closed/canceled)跟 sat_strategy/app/bot.py 透過
ccxt 輪詢真實交易所時用的字眼是一致的。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, Literal, Optional

OrderStatus = Literal["open", "closed", "canceled"]
OrderSide = Literal["buy", "sell"]


@dataclass
class Order:
    id: str
    side: OrderSide
    price: float
    qty: float
    status: OrderStatus = "open"
    filled_qty: float = 0.0


class PaperBroker:
    def __init__(self) -> None:
        self._orders: Dict[str, Order] = {}
        self._id_counter = itertools.count(1)
        self._position_qty: float = 0.0

    def place_limit_buy(self, price: float, qty: float) -> Order:
        return self._place("buy", price, qty)

    def place_limit_sell(self, price: float, qty: float) -> Order:
        return self._place("sell", price, qty)

    def _place(self, side: OrderSide, price: float, qty: float) -> Order:
        order_id = str(next(self._id_counter))
        order = Order(id=order_id, side=side, price=price, qty=qty)
        self._orders[order_id] = order
        return order

    def cancel_order(self, order_id: str) -> None:
        order = self._orders.get(order_id)
        if order is not None and order.status == "open":
            order.status = "canceled"

    def fetch_order(self, order_id: str) -> Order:
        return self._orders[order_id]

    def position_qty(self) -> float:
        return self._position_qty

    def market_close(self, qty: float) -> None:
        self._position_qty = max(0.0, self._position_qty - qty)

    def tick(self, price: float) -> None:
        """推進市場一個價格樣本,並成交所有被這個價格穿越的未成交單。"""
        for order in self._orders.values():
            if order.status != "open":
                continue
            if order.side == "buy" and price <= order.price:
                self._fill(order)
            elif order.side == "sell" and price >= order.price:
                self._fill(order)

    def _fill(self, order: Order) -> None:
        order.status = "closed"
        order.filled_qty = order.qty
        if order.side == "buy":
            self._position_qty += order.qty
        else:
            self._position_qty = max(0.0, self._position_qty - order.qty)
