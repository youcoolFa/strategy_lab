"""
所有 entry/exit/time-window 模組共用的 plugin 合約(接口)。

對應 sat_strategy/app/bot.py 裡已經隱含存在的形狀:
  - EntrySignal   ~ compute_entry_price() + _place_entry_order()
  - ExitSignal    ~ _place_exit_order()
  - TimeWindow    ~ _window_end() / _should_stop_for_cleanup()

刻意讓這些接口盡量貼近原本 bot 的方法形狀:之後要把 sat_strategy 移植到
這套架構上時,應該是「重新接上去」,而不是「重新設計」。

Phase 2 起,EntrySignal/ExitSignal 不再各自手寫 `should_enter`/
`should_exit` 布林邏輯,改成暴露一個 `rule: Condition` 屬性,由
`engine/runner.py` 呼叫 `rule.evaluate(ctx)` 決定「現在該不該觸發」。
plugin 從此只保留「觸發之後價格怎麼算」這一半的邏輯——「什麼時候觸發」
交給 rules/ 這一層。

KillSwitch 是第四種 plugin 類型:跟 TimeWindow 一樣是「什麼時候該收攤」,
但觸發原因是市場行為(價格),不是排程時間到了——這是刻意跟 TimeWindow
分開的兩個獨立合約,不是把價格判斷硬塞進 TimeWindow 裡。跟
EntrySignal/ExitSignal 不同的是,KillSwitch 觸發後不用計算任何價格,
只需要告訴 runner「該收攤了」,所以合約裡只有 `rule`,沒有價格方法。

Live 遷移 Stage 1 起,`Broker`/`OrderLike` 把 `engine/runner.py` 原本
寫死的 `PaperBroker` 型別泛化成一個 Protocol——這一步刻意不改變任何
現有行為:`PaperBroker` 現有的方法結構上已經滿足這個 Protocol,不需要
改 `PaperBroker` 一行程式碼,純粹是把隱含的合約寫明。`tick(price)`
留在合約裡,是因為 `runner.tick()` 對所有 broker 一律無條件呼叫這個
方法——`PaperBroker` 用它來模擬成交,真實交易所的 broker(見
`live/`)不需要靠外部餵價格才成交,交給它一個合法的 no-op 即可,不需
要 runner.py 為了不同 broker 種類分支處理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:
    from strategy_lab.rules.base import Condition


@dataclass
class StrategyContext:
    """決策當下傳給 plugin 的市場/執行狀態快照。"""

    now: datetime
    price: float
    price_history: Sequence[float] = field(default_factory=tuple)
    origin_price: Optional[float] = None
    entry_price: Optional[float] = None
    position_qty: float = 0.0
    entry_time: Optional[datetime] = None


@runtime_checkable
class EntrySignal(Protocol):
    rule: "Condition"

    def entry_price(self, ctx: StrategyContext) -> float: ...


@runtime_checkable
class ExitSignal(Protocol):
    rule: "Condition"

    def exit_price(self, ctx: StrategyContext) -> float: ...


@runtime_checkable
class TimeWindow(Protocol):
    def window_end(self, now: datetime) -> datetime: ...
    def should_cleanup(self, now: datetime, window_end: datetime) -> bool: ...
    def max_span(self) -> timedelta: ...


@runtime_checkable
class KillSwitch(Protocol):
    rule: "Condition"


@runtime_checkable
class OrderLike(Protocol):
    """`Broker.place_*`/`fetch_order()` 回傳的最小形狀。不要求是同一個
    具體類別——`PaperBroker` 的 `Order` dataclass 結構上已經滿足這個
    Protocol(多出來的 `side`/`qty` 欄位不影響)。"""

    id: str
    status: str  # "open" | "closed" | "canceled"
    price: float
    filled_qty: float


@runtime_checkable
class Broker(Protocol):
    """8 種下單方式(方向 buy/sell × 單種類 limit/market × 開倉/平倉),
    `broker/paper_broker.py` 的 `PaperBroker` 跟 `live/broker.py` 的
    `LiveBroker` 都要滿足同一套——見 docs/ARCHITECTURE.md §6.8。
    `place_limit_sell`/`limit_flat_buy` 是同一件事的兩個名字(前者是
    既有命名,後者符合 8 種下單方式的命名規則),兩個都要有。"""

    # --- 開倉 ---
    def place_limit_buy(self, price: float, qty: float) -> "OrderLike": ...
    def limit_sell(self, price: float, qty: float) -> "OrderLike": ...
    def market_buy(self, qty: float) -> "OrderLike": ...
    def market_sell(self, qty: float) -> "OrderLike": ...

    # --- 平倉 ---
    def place_limit_sell(self, price: float, qty: float) -> "OrderLike": ...
    def limit_flat_buy(self, price: float, qty: float) -> "OrderLike": ...
    def limit_flat_sell(self, price: float, qty: float) -> "OrderLike": ...
    def market_flat_buy(self, qty: float) -> "OrderLike": ...
    def market_flat_sell(self, qty: float) -> "OrderLike": ...

    def fetch_order(self, order_id: str) -> "OrderLike": ...
    def cancel_order(self, order_id: str) -> None: ...
    def position_qty(self) -> float: ...
    def market_close(self, qty: float) -> None: ...
    def tick(self, price: float) -> None: ...
