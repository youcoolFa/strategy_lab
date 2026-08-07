"""週末均值回歸策略的出場邏輯:回到 origin_price 就平倉。Phase 2 起,
「什麼時候觸發」交給 rules.conditions.PriceAtOrAboveReference,這個
類別只保留「觸發後出場價要算多少」。"""

from __future__ import annotations

from dataclasses import dataclass, field

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import PriceAtOrAboveReference


@register("exit", "return_to_reference")
@dataclass
class ReturnToReferenceExit:
    rule: Condition = field(init=False)

    def __post_init__(self) -> None:
        self.rule = PriceAtOrAboveReference()

    def exit_price(self, ctx: StrategyContext) -> float:
        assert ctx.origin_price is not None, "出場前必須先設定 origin_price"
        return ctx.origin_price
