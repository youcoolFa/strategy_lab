"""ReturnToReferenceExit 的鏡像版本——給平空倉用:回到 origin_price 就
回補平倉。「什麼時候觸發」交給 rules.conditions.PriceAtOrBelowReference,
這個類別只保留「觸發後出場價要算多少」——跟 ReturnToReferenceExit
完全對稱的分工,見 docs/ARCHITECTURE.md §6.11。"""

from __future__ import annotations

from dataclasses import dataclass, field

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import PriceAtOrBelowReference


@register("exit", "return_to_reference_short")
@dataclass
class ShortReturnToReferenceExit:
    rule: Condition = field(init=False)

    def __post_init__(self) -> None:
        self.rule = PriceAtOrBelowReference()

    def exit_price(self, ctx: StrategyContext) -> float:
        assert ctx.origin_price is not None, "出場前必須先設定 origin_price"
        return ctx.origin_price
