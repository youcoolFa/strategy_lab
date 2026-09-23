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
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy_lab.live.bybit_client import BybitClient, OrderResult


@dataclass
class LiveOrder:
    id: str
    status: str  # "open" | "closed" | "canceled"
    price: float
    filled_qty: float = 0.0


def _to_live_order(result: OrderResult) -> LiveOrder:
    return LiveOrder(id=result.order_id, status=result.status, price=result.price, filled_qty=result.filled_qty)


@dataclass
class LiveBroker:
    client: BybitClient
    symbol: str

    def place_limit_buy(self, price: float, qty: float) -> LiveOrder:
        result = self.client.place_limit_order(self.symbol, "Buy", qty, price, reduce_only=False)
        return _to_live_order(result)

    def place_limit_sell(self, price: float, qty: float) -> LiveOrder:
        # exit 永遠是平倉,reduce_only 寫死 True——不能因為呼叫端忘記
        # 傳而意外開出新倉。
        result = self.client.place_limit_order(self.symbol, "Sell", qty, price, reduce_only=True)
        return _to_live_order(result)

    def fetch_order(self, order_id: str) -> LiveOrder:
        result = self.client.get_order_status(self.symbol, order_id)
        return _to_live_order(result)

    def cancel_order(self, order_id: str) -> None:
        self.client.cancel_order(self.symbol, order_id)

    def position_qty(self) -> float:
        return self.client.get_position_qty(self.symbol)

    def market_close(self, qty: float) -> None:
        # 整個系統只做多(entry=買、exit=賣),市價平倉一定是賣出。
        self.client.place_market_order(self.symbol, "Sell", qty, reduce_only=True)

    def tick(self, price: float) -> None:
        pass
