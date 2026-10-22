"""區間策略的 runner(sat_strategy)。

2026-10-17 改成「各自回到起點平倉」(使用者要求,取代 2026-10-10 的「成交後在對面價位補單、多空翻轉」):

- 第一個 tick:上下各掛一張進場限價單——買 = origin × (1 − buy_pct%)、賣 = origin × (1 + sell_pct%)
  (價位由 plugins/entry/band.py 算,origin 是 start() 時的價格)。
- 哪一邊進場單成交,就**立刻在 origin 掛反向的平倉限價單**(reduce_only):多單用賣單平、空單用買單平。
  另一邊的進場單繼續掛著、不撤。
- 平倉單成交 → 把這一邊的進場單**補回去**,回到最初狀態(兩邊進場單都掛著)。可以一直循環,沒有次數上限
  (loop 必須是 null);同一時間最多一筆持倉:要走到另一邊的進場價,價格一定先經過 origin、平倉單先成交。
- 同一個 tick 裡有好幾張單成交(價格一下子衝過整個區間):先處理平倉單,再處理進場單,
  持倉紀錄、損益才會算在對的價位。
- 沒有停損,直到時間窗結束(或 kill switch / 手動停止)才收尾:取消所有掛單、市價平倉。
- 被外部取消的單(例如在 Bybit App 手動取消)→ 進場單原價位重新掛回去;平倉單如果部位還在就在 origin 重新掛,
  部位已經不在了(例如手動平掉)就不能再掛 reduce_only 的單,改成把進場單補回去。

單的用途照 sl_order.purpose 的定義:進場單 = entry、平倉單 = exit(收尾市價平倉由 runner._cleanup 記)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from strategy_lab.engine.runner import RunState, StrategyRunner
from strategy_lab.interfaces import OrderLike

ENTRY = "entry"
EXIT = "exit"


def _opposite(side: str) -> str:
    return "Sell" if side == "Buy" else "Buy"


@dataclass
class BandRunner(StrategyRunner):
    buy_price: Optional[float] = field(default=None, init=False)
    sell_price: Optional[float] = field(default=None, init=False)
    resting: List[Tuple[str, str, OrderLike]] = field(default_factory=list, init=False)  # (方向, entry/exit, 交易所上的單)

    def __post_init__(self) -> None:
        if not getattr(self.entry, "band", False):
            raise ValueError(f"BandRunner 只能搭配區間策略的 entry(type: band),收到 {type(self.entry).__name__}")
        super().__post_init__()

    def tick(self, now: datetime, price: float) -> None:
        if not self._prelude(now, price):
            return
        if self.state == RunState.IDLE:
            self.buy_price, self.sell_price = self.entry.prices(self.origin_price)
            self._place_entry(now, "Buy")
            self._place_entry(now, "Sell")
            self.state = RunState.ENTRY_PENDING
            return
        self._sync(now)

    def _place_entry(self, now: datetime, side: str) -> None:
        """進場單:買 = origin 往下、賣 = origin 往上;不是 reduce_only。"""
        if side == "Buy":
            price = self.buy_price
            order = self.broker.place_limit_buy(price=price, qty=self.order_qty)
        else:
            price = self.sell_price
            order = self.broker.limit_sell(price=price, qty=self.order_qty)
        self.resting.append((side, ENTRY, order))
        self._emit_order(now, ENTRY, order, side, "limit", price, self.order_qty, reduce_only=False, status="open")

    def _place_exit(self, now: datetime, side: str) -> None:
        """平倉單:價位一律是 origin,reduce_only。side 是這張單的方向——多單用 Sell 平、空單用 Buy 平。"""
        price = self.origin_price
        if side == "Sell":
            order = self.broker.place_limit_sell(price=price, qty=self.order_qty)  # 平多倉
        else:
            order = self.broker.limit_flat_sell(price=price, qty=self.order_qty)  # 平空倉
        self.resting.append((side, EXIT, order))
        self._emit_order(now, EXIT, order, side, "limit", price, self.order_qty, reduce_only=True, status="open")

    def _sync(self, now: datetime) -> None:
        fetched = [(side, kind, order, self.broker.fetch_order(order.id)) for side, kind, order in list(self.resting)]
        # 同一個 tick 好幾張單一起成交 = 價格衝過了整個區間:平倉單(在 origin)一定先於另一邊的進場單成交
        fetched.sort(key=lambda item: item[1] != EXIT)
        for side, kind, order, status in fetched:
            if status.status == "closed":
                self.resting.remove((side, kind, order))
                qty = status.filled_qty or self.order_qty
                self._update_order(now, order.id, "closed", status.price, qty)
                if self.entry_time is None:
                    self.entry_time = now
                self._record_fill(now, 1 if side == "Buy" else -1, qty, status.price)
                if kind == ENTRY:
                    self._place_exit(now, _opposite(side))  # 立刻在起點掛反向的平倉單
                else:
                    self._place_entry(now, _opposite(side))  # 這一輪平完了:把這一邊的進場單補回去
            elif status.status == "canceled":
                self.resting.remove((side, kind, order))
                self._update_order(now, order.id, "canceled", None, status.filled_qty)
                if kind == ENTRY:
                    self._place_entry(now, side)  # 被外部取消 → 原價位重新掛回去
                elif self.broker.position_qty() != 0:
                    self._place_exit(now, side)  # 部位還在 → 在起點重新掛平倉單
                else:
                    self._place_entry(now, _opposite(side))  # 部位已經不在:不能掛 reduce_only,改補進場單
        self.state = RunState.IN_POSITION if self.broker.position_qty() != 0 else RunState.ENTRY_PENDING

    def _cancel_open_orders(self, now: datetime) -> None:
        for _, _, order in self.resting:
            self.broker.cancel_order(order.id)
            self._update_order(now, order.id, "canceled", None, 0.0)
        self.resting = []
