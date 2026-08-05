"""
所有 entry/exit/time-window 模組共用的 plugin 合約(接口)。

對應 sat_strategy/app/bot.py 裡已經隱含存在的形狀:
  - EntrySignal   ~ compute_entry_price() + _place_entry_order()
  - ExitSignal    ~ _place_exit_order()
  - TimeWindow    ~ _window_end() / _should_stop_for_cleanup()

刻意讓這些接口盡量貼近原本 bot 的方法形狀:之後要把 sat_strategy 移植到
這套架構上時,應該是「重新接上去」,而不是「重新設計」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol, Sequence, runtime_checkable


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
    def should_enter(self, ctx: StrategyContext) -> bool: ...
    def entry_price(self, ctx: StrategyContext) -> float: ...


@runtime_checkable
class ExitSignal(Protocol):
    def should_exit(self, ctx: StrategyContext) -> bool: ...
    def exit_price(self, ctx: StrategyContext) -> float: ...


@runtime_checkable
class TimeWindow(Protocol):
    def window_end(self, now: datetime) -> datetime: ...
    def should_cleanup(self, now: datetime, window_end: datetime) -> bool: ...
