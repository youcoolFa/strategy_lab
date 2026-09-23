"""
主迴圈(orchestrator loop),泛化自 sat_strategy/app/bot.py 的
SatStrategyBot.run():idle → 下單進場 → 成交 → 下單出場 → 成交 →
回到 idle,由 TimeWindow 把關開始/結束。

`broker` 欄位型別是 `interfaces.Broker` 這個 Protocol,不是寫死
`PaperBroker`——這個檔案完全不知道、也不需要知道背後是模擬交易所還是
真實交易所(見 `live/`),只透過 `Broker` 合約的 7 個方法互動。預設值
仍然是 `PaperBroker()`,既有行為完全不變。

比 bot.py 多做的一個泛化:bot.py 一旦進場成交,會「立刻」下出場單。
這裡則是只有在 exit.rule.evaluate(ctx) 為真的時候才下出場單,所以像
MA 交叉策略的止盈止損括號單這種,可以先等訊號觸發,不用一成交就馬上
掛單等。

Phase 2 起,「該不該進場/出場」不再是 plugin 自己手寫的
`should_enter`/`should_exit` 布林方法,而是統一呼叫
`entry.rule.evaluate(ctx)` / `exit.rule.evaluate(ctx)` —— `rule` 是
rules/ 這一層組出來的 Condition 樹(見 strategy_lab/interfaces.py 的
`EntrySignal`/`ExitSignal`)。這個檔案完全不需要知道 Condition 樹長
什麼樣子,只需要知道它有 `.evaluate(ctx) -> bool`。

`kill_switch`(選填)是第四種、跟 `time_window` 平行的「該不該收攤」
判斷,差別只在觸發原因:`time_window` 管排程時間,`kill_switch` 管市場
行為(價格)。兩者都觸發同一個 `_cleanup()`,`tick()` 裡依序檢查,任一個
成立就收攤,不需要分辨是哪一個觸發的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Iterator, List, Optional

from strategy_lab.broker.paper_broker import PaperBroker
from strategy_lab.interfaces import Broker, EntrySignal, ExitSignal, KillSwitch, OrderLike, StrategyContext, TimeWindow


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
    broker: Broker = field(default_factory=PaperBroker)
    kill_switch: Optional[KillSwitch] = None

    state: RunState = field(default=RunState.IDLE, init=False)
    window_end: Optional[datetime] = field(default=None, init=False)
    origin_price: Optional[float] = field(default=None, init=False)
    entry_order: Optional[OrderLike] = field(default=None, init=False)
    exit_order: Optional[OrderLike] = field(default=None, init=False)
    active_entry_price: Optional[float] = field(default=None, init=False)
    price_history: List[float] = field(default_factory=list, init=False)
    trades: List[Trade] = field(default_factory=list, init=False)
    entry_time: Optional[datetime] = field(default=None, init=False)

    def start(self, now: datetime, price: float) -> None:
        """在整個窗口期間只捕捉一次 origin_price 與 window_end,跟
        bot.py 的 run() 一致(origin_price/entry_price 是在 while 迴圈
        「開始之前」算好的,不會每次進出場循環都重算一次)。"""
        self.origin_price = price
        self.window_end = self.time_window.window_end(now)
        self.state = RunState.IDLE

    def tick(self, now: datetime, price: float) -> None:
        """每收到一個新價格就執行一次，根據目前狀態決定下一步動作。"""

        if self.state == RunState.STOPPED:
            return

        self.price_history.append(price)
        self.broker.tick(price)

        assert self.window_end is not None, "呼叫 tick() 前必須先呼叫 start()"
        if self.time_window.should_cleanup(now, self.window_end):
            self._cleanup()
            return

        if self.kill_switch is not None and self.kill_switch.rule.evaluate(self._ctx(now, price)):
            self._cleanup()
            return

        if self.state == RunState.IDLE:
            self._try_enter(now, price)
        elif self.state == RunState.ENTRY_PENDING:
            self._check_entry_fill(now)
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
        """建立當前策略上下文，供 rule.evaluate() 使用。"""
        return StrategyContext(
            now=now,
            price=price,
            price_history=tuple(self.price_history),
            origin_price=self.origin_price,
            entry_price=self.active_entry_price,
            position_qty=self.broker.position_qty(),
            entry_time=self.entry_time,
        )

    def _try_enter(self, now: datetime, price: float) -> None:
        """檢查是否滿足進場條件，若滿足則下進場限價單。"""

        ctx = self._ctx(now, price)
        if self.entry.rule.evaluate(ctx):
            self.entry_order = self.broker.place_limit_buy(price=self.entry.entry_price(ctx), qty=self.order_qty)
            self.state = RunState.ENTRY_PENDING

    def _check_entry_fill(self, now: datetime) -> None:
        """檢查進場單是否已成交或被取消。"""

        assert self.entry_order is not None
        order = self.broker.fetch_order(self.entry_order.id)
        if order.status == "closed":
            self.active_entry_price = order.price
            self.entry_time = now
            self.state = RunState.IN_POSITION
        elif order.status == "canceled":
            self.state = RunState.IDLE

    def _try_exit(self, now: datetime, price: float) -> None:
        """檢查是否滿足進場條件，若滿足則下進場限價單。"""
        ctx = self._ctx(now, price)
        if self.exit.rule.evaluate(ctx):
            qty = self.broker.position_qty()
            self.exit_order = self.broker.place_limit_sell(price=self.exit.exit_price(ctx), qty=qty)
            self.state = RunState.EXIT_PENDING

    def _check_exit_fill(self) -> None:
        """檢查出場單是否已成交或被取消。"""
        assert self.exit_order is not None
        order = self.broker.fetch_order(self.exit_order.id)
        if order.status == "closed":
            assert self.active_entry_price is not None
            self.trades.append(Trade(entry_price=self.active_entry_price, exit_price=order.price, qty=order.filled_qty))
            self.active_entry_price = None
            self.entry_time = None
            self.state = RunState.IDLE
        elif order.status == "canceled":
            self.state = RunState.IN_POSITION

    def _cleanup(self) -> None:
        """時間窗口結束時，取消所有未成交單並強制平倉。"""
        for order in (self.entry_order, self.exit_order):
            if order is not None:
                self.broker.cancel_order(order.id)
        remaining = self.broker.position_qty()
        if remaining > 0:
            self.broker.market_close(remaining)
        self.state = RunState.STOPPED
