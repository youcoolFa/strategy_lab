"""
主迴圈(orchestrator loop),泛化自 sat_strategy/app/bot.py 的
SatStrategyBot.run():idle → 下單進場 → 成交 → 下單出場 → 成交 →
回到 idle,由 TimeWindow 把關開始/結束,對象是 PaperBroker,不是透過
ccxt 呼叫真實交易所。

比 bot.py 多做的一個泛化:bot.py 一旦進場成交,會「立刻」下出場單
(它的出場條件是無條件成立的——「回到 origin」)。這裡則是只有在
exit.should_exit(ctx) 為真的時候才下出場單,所以像 MA 交叉策略的
止盈止損括號單這種,可以先等訊號觸發,不用一成交就馬上掛單等。對
return_to_reference(永遠為真)來說,行為會退化成跟 bot.py 完全一樣。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Iterator, List, Optional

from strategy_lab.broker.paper_broker import Order, PaperBroker
from strategy_lab.interfaces import EntrySignal, ExitSignal, StrategyContext, TimeWindow


class RunState(Enum):
    IDLE = auto()
    ENTRY_PENDING = auto()
    IN_POSITION = auto()
    EXIT_PENDING = auto()
    STOPPED = auto()


@dataclass
class Trade:
    entry_price: float
    exit_price: float
    qty: float


@dataclass
class StrategyRunner:
    entry: EntrySignal
    exit: ExitSignal
    time_window: TimeWindow
    order_qty: float
    broker: PaperBroker = field(default_factory=PaperBroker)

    state: RunState = field(default=RunState.IDLE, init=False)
    window_end: Optional[datetime] = field(default=None, init=False)
    origin_price: Optional[float] = field(default=None, init=False)
    entry_order: Optional[Order] = field(default=None, init=False)
    exit_order: Optional[Order] = field(default=None, init=False)
    active_entry_price: Optional[float] = field(default=None, init=False)
    price_history: List[float] = field(default_factory=list, init=False)
    trades: List[Trade] = field(default_factory=list, init=False)

    def start(self, now: datetime, price: float) -> None:
        """在整個窗口期間只捕捉一次 origin_price 與 window_end,跟
        bot.py 的 run() 一致(origin_price/entry_price 是在 while 迴圈
        「開始之前」算好的,不會每次進出場循環都重算一次)。"""
        self.origin_price = price
        self.window_end = self.time_window.window_end(now)
        self.state = RunState.IDLE

    def tick(self, now: datetime, price: float) -> None:
        if self.state == RunState.STOPPED:
            return

        self.price_history.append(price)
        self.broker.tick(price)

        assert self.window_end is not None, "呼叫 tick() 前必須先呼叫 start()"
        if self.time_window.should_cleanup(now, self.window_end):
            self._cleanup()
            return

        if self.state == RunState.IDLE:
            self._try_enter(now, price)
        elif self.state == RunState.ENTRY_PENDING:
            self._check_entry_fill()
        elif self.state == RunState.IN_POSITION:
            self._try_exit(now, price)
        elif self.state == RunState.EXIT_PENDING:
            self._check_exit_fill()

    def run(
        self,
        now: datetime,
        feed: Iterator[float],
        tick_interval: timedelta,
        max_ticks: int = 100_000,
    ) -> None:
        """給 demo 用的便利迴圈:從一個價格 feed 持續驅動 tick(),直到
        TimeWindow 觸發 cleanup、狀態走到 STOPPED 為止。"""
        price = next(feed)
        self.start(now, price)
        self.tick(now, price)
        for _ in range(max_ticks - 1):
            if self.state == RunState.STOPPED:
                return
            now += tick_interval
            price = next(feed)
            self.tick(now, price)
        if self.state != RunState.STOPPED:
            raise RuntimeError("run() 超過 max_ticks 仍未進入 STOPPED - 請檢查 time_window 設定")

    def _ctx(self, now: datetime, price: float) -> StrategyContext:
        return StrategyContext(
            now=now,
            price=price,
            price_history=tuple(self.price_history),
            origin_price=self.origin_price,
            entry_price=self.active_entry_price,
            position_qty=self.broker.position_qty(),
        )

    def _try_enter(self, now: datetime, price: float) -> None:
        ctx = self._ctx(now, price)
        if self.entry.should_enter(ctx):
            self.entry_order = self.broker.place_limit_buy(price=self.entry.entry_price(ctx), qty=self.order_qty)
            self.state = RunState.ENTRY_PENDING

    def _check_entry_fill(self) -> None:
        assert self.entry_order is not None
        order = self.broker.fetch_order(self.entry_order.id)
        if order.status == "closed":
            self.active_entry_price = order.price
            self.state = RunState.IN_POSITION
        elif order.status == "canceled":
            self.state = RunState.IDLE

    def _try_exit(self, now: datetime, price: float) -> None:
        ctx = self._ctx(now, price)
        if self.exit.should_exit(ctx):
            qty = self.broker.position_qty()
            self.exit_order = self.broker.place_limit_sell(price=self.exit.exit_price(ctx), qty=qty)
            self.state = RunState.EXIT_PENDING

    def _check_exit_fill(self) -> None:
        assert self.exit_order is not None
        order = self.broker.fetch_order(self.exit_order.id)
        if order.status == "closed":
            assert self.active_entry_price is not None
            self.trades.append(Trade(entry_price=self.active_entry_price, exit_price=order.price, qty=order.filled_qty))
            self.active_entry_price = None
            self.state = RunState.IDLE
        elif order.status == "canceled":
            self.state = RunState.IN_POSITION

    def _cleanup(self) -> None:
        for order in (self.entry_order, self.exit_order):
            if order is not None:
                self.broker.cancel_order(order.id)
        remaining = self.broker.position_qty()
        if remaining > 0:
            self.broker.market_close(remaining)
        self.state = RunState.STOPPED
