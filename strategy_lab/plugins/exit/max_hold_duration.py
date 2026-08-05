"""持倉超過指定分鐘數就強制出場的 ExitSignal。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register


@register("exit", "max_hold_duration")
@dataclass
class MaxHoldDurationExit:
    max_minutes: int

    def should_exit(self, ctx: StrategyContext) -> bool:
        if ctx.entry_time is None:
            return False
        elapsed = ctx.now - ctx.entry_time
        return elapsed >= timedelta(minutes=self.max_minutes)

    def exit_price(self, ctx: StrategyContext) -> float:
        return ctx.price
