"""
Orchestrator loop, generalized from sat_strategy/app/bot.py's
SatStrategyBot.run(): idle -> entry placed -> filled -> exit placed ->
filled -> idle, gated by a TimeWindow, executed against a PaperBroker
instead of a real exchange via ccxt.

One generalization beyond bot.py: bot.py always places the exit order
immediately after the entry fills (its exit condition is unconditionally
true — "return to origin"). Here, an exit order is only placed once
exit.should_exit(ctx) is true, so a strategy like MA-crossover's bracket
TP/SL can wait for its trigger instead of resting an order immediately.
For return_to_reference (always true), this reduces to exactly bot.py's
behavior.
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
        """Capture origin_price and window_end ONCE for the whole window,
        matching bot.py's run() (origin_price/entry_price are computed
        before the while-loop, not recomputed each entry/exit cycle)."""
        self.origin_price = price
        self.window_end = self.time_window.window_end(now)
        self.state = RunState.IDLE

    def tick(self, now: datetime, price: float) -> None:
        if self.state == RunState.STOPPED:
            return

        self.price_history.append(price)
        self.broker.tick(price)

        assert self.window_end is not None, "call start() before tick()"
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
        """Convenience loop for demos: drives tick() from a price feed
        until the TimeWindow triggers cleanup and STOPPED is reached."""
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
            raise RuntimeError("run() exceeded max_ticks without reaching STOPPED - check time_window config")

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
