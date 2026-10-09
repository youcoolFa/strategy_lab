"""區間策略的 runner(weekend_band_reversion,2026-10-10 改版)。

- 第一個 tick:上下各掛一張限價單——買 = origin × (1 − buy_pct%)、賣 = origin × (1 + sell_pct%)
  (價位由 plugins/entry/band.py 算,origin 是 start() 時的價格)。
- 每成交一張,就在**對面價位**補一張同數量的單。單向持倉:買賣單是淨部位的加減,所以
  持倉 +1 時對面有兩張賣單,價格到了兩張都成交 = 平掉多單 + 開出空單 → −1;反之亦然。
  第一張成交後持倉就在 +1 / −1 之間切換,每次穿過整個區間賺一次價差。
- 每張單都不是 reduceOnly:同一個價位的兩張賣單,交易所成交順序不一定,reduceOnly 那張
  若排在後面會因為部位已經歸 0 被取消,翻不成空單。
- 沒有停損、不限次數(loop 必須是 null),直到時間窗結束(或 kill switch / 手動停止)
  才收尾:取消所有掛單、市價平倉。
- 被外部取消的單(例如在 Bybit App 手動取消)→ 原價位原方向重新掛回去。

每張單在紀錄上的用途是 "band"(同一張單成交時是平倉還是開倉,要看當時的持倉)。
成交照順序一筆一筆記進 EventTracker:多單 +1 → 一張賣單成交 = 這一輪結束;下一張賣單 = 新的一輪(空)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from strategy_lab.engine.runner import RunState, StrategyRunner
from strategy_lab.interfaces import OrderLike

PURPOSE = "band"


@dataclass
class BandRunner(StrategyRunner):
    buy_price: Optional[float] = field(default=None, init=False)
    sell_price: Optional[float] = field(default=None, init=False)
    resting: List[Tuple[str, OrderLike]] = field(default_factory=list, init=False)  # (side, 交易所上的單)

    def __post_init__(self) -> None:
        if not getattr(self.entry, "band", False):
            raise ValueError(f"BandRunner 只能搭配區間策略的 entry(type: band),收到 {type(self.entry).__name__}")
        super().__post_init__()

    def tick(self, now: datetime, price: float) -> None:
        if not self._prelude(now, price):
            return
        if self.state == RunState.IDLE:
            self.buy_price, self.sell_price = self.entry.prices(self.origin_price)
            self._place(now, "Buy")
            self._place(now, "Sell")
            self.state = RunState.ENTRY_PENDING
            return
        self._sync(now)

    def _place(self, now: datetime, side: str) -> None:
        if side == "Buy":
            price = self.buy_price
            order = self.broker.place_limit_buy(price=price, qty=self.order_qty)
        else:
            price = self.sell_price
            order = self.broker.limit_sell(price=price, qty=self.order_qty)
        self.resting.append((side, order))
        self._emit_order(now, PURPOSE, order, side, "limit", price, self.order_qty, reduce_only=False, status="open")

    def _sync(self, now: datetime) -> None:
        for side, order in list(self.resting):
            fetched = self.broker.fetch_order(order.id)
            if fetched.status == "closed":
                self.resting.remove((side, order))
                qty = fetched.filled_qty or self.order_qty
                self._update_order(now, order.id, "closed", fetched.price, qty)
                if self.entry_time is None:
                    self.entry_time = now
                self._record_fill(now, 1 if side == "Buy" else -1, qty, fetched.price)
                self._place(now, "Sell" if side == "Buy" else "Buy")  # 對面補一張
            elif fetched.status == "canceled":
                self.resting.remove((side, order))
                self._update_order(now, order.id, "canceled", None, fetched.filled_qty)
                self._place(now, side)  # 被外部取消 → 原價位重新掛回去
        self.state = RunState.IN_POSITION if self.broker.position_qty() != 0 else RunState.ENTRY_PENDING

    def _cancel_open_orders(self, now: datetime) -> None:
        for _, order in self.resting:
            self.broker.cancel_order(order.id)
            self._update_order(now, order.id, "canceled", None, 0.0)
        self.resting = []
