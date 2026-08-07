"""持倉超過指定分鐘數就強制出場的 ExitSignal。Phase 2 起,「什麼時候
觸發」交給 rules.conditions.MaxDurationElapsed,這個類別只保留「觸發後
出場價要算多少」。"""

from __future__ import annotations

from dataclasses import dataclass, field

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import MaxDurationElapsed


@register("exit", "max_hold_duration")
@dataclass
class MaxHoldDurationExit:
    max_minutes: int
    rule: Condition = field(init=False)

    def __post_init__(self) -> None:
        self.rule = MaxDurationElapsed(self.max_minutes)

    def exit_price(self, ctx: StrategyContext) -> float:
        return ctx.price
