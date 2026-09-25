"""
記憶體內的模擬交易所。沒有任何網路呼叫,不需要 API key。

`tick(price)` 被呼叫、價格穿越限價單的價位時就會成交——buy 在價格
「低於或等於」限價時成交,sell 在價格「高於或等於」限價時成交。市價單
不用等價格穿越,下單當下(`tick()` 剛更新過 `_last_price`)就立刻成交,
呼應真實交易所「市價單保證立刻成交」的語意。狀態用的字眼
(open/closed/canceled)跟 sat_strategy/app/bot.py 透過 ccxt 輪詢真實
交易所時用的字眼是一致的。

8 種下單方式(方向 buy/sell × 單種類 limit/market × 開倉/平倉),對稱於
live/broker.py 的 LiveBroker——見 docs/ARCHITECTURE.md §6.8:
    place_limit_buy   開多倉,限價,reduce_only=False
    limit_sell        開空倉,限價,reduce_only=False
    market_buy        開多倉,市價,reduce_only=False
    market_sell       開空倉,市價,reduce_only=False
    place_limit_sell  平多倉,限價,reduce_only=True(= limit_flat_buy)
    limit_flat_sell   平空倉,限價,reduce_only=True
    market_flat_buy   平多倉,市價,reduce_only=True
    market_flat_sell  平空倉,市價,reduce_only=True

`reduce_only=True` 的單,成交後部位不會「穿越」0(多倉最多平到 0、
空倉最多回補到 0),模擬真實交易所 reduceOnly 參數的行為——這是刻意的,
不是疏漏:如果讓它穿越,沙盒測出來的部位軌跡會跟真實環境對不上。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, Literal, Optional

OrderStatus = Literal["open", "closed", "canceled"]
OrderSide = Literal["buy", "sell"]
OrderType = Literal["limit", "market"]


@dataclass
class Order:
    id: str
    side: OrderSide
    price: float
    qty: float
    status: OrderStatus = "open"
    filled_qty: float = 0.0
    order_type: OrderType = "limit"
    reduce_only: bool = False


class PaperBroker:
    def __init__(self) -> None:
        self._orders: Dict[str, Order] = {}
        self._id_counter = itertools.count(1)
        self._position_qty: float = 0.0
        self._last_price: float = 0.0

    # --- 開倉 ---
    def place_limit_buy(self, price: float, qty: float) -> Order:
        return self._place("buy", price, qty, order_type="limit", reduce_only=False)

    def limit_sell(self, price: float, qty: float) -> Order:
        return self._place("sell", price, qty, order_type="limit", reduce_only=False)

    def market_buy(self, qty: float) -> Order:
        return self._place("buy", self._last_price, qty, order_type="market", reduce_only=False)

    def market_sell(self, qty: float) -> Order:
        return self._place("sell", self._last_price, qty, order_type="market", reduce_only=False)

    # --- 平倉 ---
    def place_limit_sell(self, price: float, qty: float) -> Order:
        return self._place("sell", price, qty, order_type="limit", reduce_only=True)

    def limit_flat_buy(self, price: float, qty: float) -> Order:
        """跟 place_limit_sell 是同一件事——保留 place_limit_sell 是因為
        runner.py/既有測試已經在用這個名字,這裡另外提供符合 8 種下單
        方式命名規則的別名。"""
        return self.place_limit_sell(price, qty)

    def limit_flat_sell(self, price: float, qty: float) -> Order:
        return self._place("buy", price, qty, order_type="limit", reduce_only=True)

    def market_flat_buy(self, qty: float) -> Order:
        return self._place("sell", self._last_price, qty, order_type="market", reduce_only=True)

    def market_flat_sell(self, qty: float) -> Order:
        return self._place("buy", self._last_price, qty, order_type="market", reduce_only=True)

    def _place(self, side: OrderSide, price: float, qty: float, order_type: OrderType, reduce_only: bool) -> Order:
        order_id = str(next(self._id_counter))
        order = Order(id=order_id, side=side, price=price, qty=qty, order_type=order_type, reduce_only=reduce_only)
        self._orders[order_id] = order
        if order_type == "market":
            self._fill(order)  # 市價單不用等 tick() 價格穿越,下單當下立刻成交
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
        """_cleanup() 用的既有方法,不屬於 8 種命名方式的一部分,行為
        維持完全不變(不回傳 OrderLike,呼叫端本來就不需要)。"""
        self._position_qty = max(0.0, self._position_qty - qty)

    def tick(self, price: float) -> None:
        """推進市場一個價格樣本,更新市價單要用的參考價,並成交所有被
        這個價格穿越的未成交限價單(市價單在下單當下就已經成交,這裡
        會直接跳過)。"""
        self._last_price = price
        for order in self._orders.values():
            if order.status != "open" or order.order_type == "market":
                continue
            if order.side == "buy" and price <= order.price:
                self._fill(order)
            elif order.side == "sell" and price >= order.price:
                self._fill(order)

    def _fill(self, order: Order) -> None:
        order.status = "closed"
        order.filled_qty = order.qty
        delta = order.qty if order.side == "buy" else -order.qty
        new_qty = self._position_qty + delta
        if order.reduce_only:
            # 平倉單不該讓部位穿越 0:多倉最多平到 0、空倉最多回補到 0,
            # 不會意外開出反方向的新倉。
            if self._position_qty > 0:
                new_qty = max(0.0, new_qty)
            elif self._position_qty < 0:
                new_qty = min(0.0, new_qty)
        self._position_qty = new_qty
