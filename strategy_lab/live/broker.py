"""
live/broker.py

`LiveBroker` 把 `BybitClient`(需要 symbol/side、知道自己是哪個交易對)
包裝成滿足 `interfaces.Broker` 這個通用合約(只認 price/qty,不知道
symbol 是什麼)的物件——跟 `broker/paper_broker.py` 的 `PaperBroker`
是同一種角色,一個是真實版,一個是模擬版,`engine/runner.py` 兩者都能
接,不需要知道背後是哪一種。

`symbol` 存在 `LiveBroker` 自己身上(建構時決定,整個 runner 的生命
週期不會變),不是每次呼叫都要傳——這是 `Broker` 合約的方法簽名
(`place_limit_buy(price, qty)`)天生就沒有 symbol 參數的原因。

`tick(price)` 是刻意的 no-op:真實交易所自己在背景撮合訂單,不需要
外部餵價格才成交——這跟 `PaperBroker.tick()` 的語意完全不同,但兩者
都滿足同一個 `Broker` Protocol,`runner.py` 不需要、也不應該知道這個
差異。

`dry_run`(預設 `True`,安全預設)對應 sat_strategy/app/bot.py 每個下單
方法前的 `if self.config.dry_run: return ...`。放在 `LiveBroker` 而不是
`BybitClient`:`BybitClient` 保持忠實、無條件包裝真實 API(即使在模擬
模式下,可能還是想用它查真實價格);「要不要真的下單」的決策屬於
`LiveBroker`,它才是 bot.py `_place_entry_order`/`_place_exit_order`
這些方法的對應位置。

dry-run 訂單在**第一次被 `fetch_order()` 查詢時**才從 open 變成
closed(不是下單當下就立即成交)——呼應 bot.py 的假設:「dry-run 模式
沒有真的交易所可以查,直接視為立即成交,方便測試整體流程」,那個假設
在 bot.py 是在輪詢迴圈裡兌現的,這裡搬到 `fetch_order()` 兌現,行為
等價:runner.py 下單後一定會在下一次 tick 呼叫 `fetch_order()` 才會
知道有沒有成交,所以「下單當下」跟「第一次查詢時」對 runner.py 來說
沒有可觀察的差異。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, Optional

from loguru import logger

from strategy_lab.live.bybit_client import BybitClient, OrderResult


@dataclass
class LiveOrder:
    id: str
    status: str  # "open" | "closed" | "canceled"
    price: float
    side: Optional[str] = None
    qty: float = 0.0
    filled_qty: float = 0.0


def _to_live_order(result: OrderResult) -> LiveOrder:
    return LiveOrder(id=result.order_id, status=result.status, price=result.price, filled_qty=result.filled_qty)


@dataclass
class LiveBroker:
    client: BybitClient
    symbol: str
    dry_run: bool = True

    _dry_run_orders: Dict[str, LiveOrder] = field(default_factory=dict, init=False)
    _dry_run_position_qty: float = field(default=0.0, init=False)
    _dry_run_id_counter: itertools.count = field(default_factory=lambda: itertools.count(1), init=False)

    def place_limit_buy(self, price: float, qty: float) -> LiveOrder:
        if self.dry_run:
            return self._dry_run_place("Buy", price, qty)
        result = self.client.place_limit_order(self.symbol, "Buy", qty, price, reduce_only=False)
        return _to_live_order(result)

    def place_limit_sell(self, price: float, qty: float) -> LiveOrder:
        # exit 永遠是平倉,reduce_only 寫死 True——不能因為呼叫端忘記
        # 傳而意外開出新倉。
        if self.dry_run:
            return self._dry_run_place("Sell", price, qty)
        result = self.client.place_limit_order(self.symbol, "Sell", qty, price, reduce_only=True)
        return _to_live_order(result)

    def fetch_order(self, order_id: str) -> LiveOrder:
        if order_id in self._dry_run_orders:
            return self._dry_run_fill_if_pending(order_id)
        result = self.client.get_order_status(self.symbol, order_id)
        return _to_live_order(result)

    def cancel_order(self, order_id: str) -> None:
        if order_id in self._dry_run_orders:
            order = self._dry_run_orders[order_id]
            if order.status == "open":
                logger.info(f"[dry-run] 模擬取消訂單 {order_id}")
                order.status = "canceled"
            return
        self.client.cancel_order(self.symbol, order_id)

    def position_qty(self) -> float:
        if self.dry_run:
            return self._dry_run_position_qty
        return self.client.get_position_qty(self.symbol)

    def market_close(self, qty: float) -> None:
        # 整個系統只做多(entry=買、exit=賣),市價平倉一定是賣出。
        if self.dry_run:
            logger.info(f"[dry-run] 模擬市價平倉 {self.symbol} qty={qty:.4f}")
            self._dry_run_position_qty = max(0.0, self._dry_run_position_qty - qty)
            return
        self.client.place_market_order(self.symbol, "Sell", qty, reduce_only=True)

    def tick(self, price: float) -> None:
        pass

    # ------------------------------------------------------------------
    # dry-run 內部模擬
    # ------------------------------------------------------------------
    def _dry_run_place(self, side: str, price: float, qty: float) -> LiveOrder:
        order_id = f"dry-run-{next(self._dry_run_id_counter)}"
        order = LiveOrder(id=order_id, status="open", price=price, side=side, qty=qty)
        self._dry_run_orders[order_id] = order
        logger.info(f"[dry-run] 模擬下 {side} 單:{self.symbol} @ {price:.2f} qty={qty:.4f}")
        return order

    def _dry_run_fill_if_pending(self, order_id: str) -> LiveOrder:
        order = self._dry_run_orders[order_id]
        if order.status == "open":
            order.status = "closed"
            order.filled_qty = order.qty
            self._dry_run_position_qty += order.qty if order.side == "Buy" else -order.qty
        return order
