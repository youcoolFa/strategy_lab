"""MA 均線交叉策略的進場邏輯:快線由下往上穿越慢線時買進。Phase 2 起,
「什麼時候觸發」交給 rules.conditions.MovingAverageCross,這個類別只
保留「觸發後進場價要算多少」——由於是觸發即進場,直接用目前市價。"""

from __future__ import annotations

from dataclasses import dataclass, field

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import MovingAverageCross


@register("entry", "ma_crossover")
@dataclass
class MACrossoverEntry:
    fast_window: int = 5
    slow_window: int = 20
    rule: Condition = field(init=False)

    def __post_init__(self) -> None:
        self.rule = MovingAverageCross(self.fast_window, self.slow_window)

    def entry_price(self, ctx: StrategyContext) -> float:
        return ctx.price
