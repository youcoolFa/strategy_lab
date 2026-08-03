"""
Plugin contracts shared by every entry/exit/time-window module.

Mirrors the shapes already implicit in sat_strategy/app/bot.py:
  - EntrySignal   ~ compute_entry_price() + _place_entry_order()
  - ExitSignal    ~ _place_exit_order()
  - TimeWindow    ~ _window_end() / _should_stop_for_cleanup()

Keeping the interfaces this close to the original bot's method shapes is
deliberate: porting sat_strategy onto this architecture later should be a
re-plug, not a redesign.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol, Sequence, runtime_checkable


@dataclass
class StrategyContext:
    """Snapshot of market/run state passed to plugins at decision time."""

    now: datetime
    price: float
    price_history: Sequence[float] = field(default_factory=tuple)
    origin_price: Optional[float] = None
    entry_price: Optional[float] = None
    position_qty: float = 0.0


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
