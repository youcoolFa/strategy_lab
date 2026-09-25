"""DeviationFromReferenceEntry 的鏡像版本——給開空倉用:賣出(做空)
條件是價格漲破 origin_price 的 deviation_pct%。「什麼時候觸發」交給
rules.conditions.PriceAboveReference,這個類別只保留「觸發後掛單價要
算多少」——跟 DeviationFromReferenceEntry 完全對稱的分工,見
docs/ARCHITECTURE.md §6.11。"""

from __future__ import annotations

from dataclasses import dataclass, field

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import PriceAboveReference


def compute_short_entry_price(reference_price: float, deviation_pct: float) -> float:
    """compute_entry_price() 的鏡像版本:往上算,不是往下算。"""
    return round(reference_price * (1 + deviation_pct / 100), 2)


@register("entry", "deviation_from_reference_short")
@dataclass
class ShortDeviationFromReferenceEntry:
    deviation_pct: float
    rule: Condition = field(init=False)

    def __post_init__(self) -> None:
        self.rule = PriceAboveReference(self.deviation_pct)

    def entry_price(self, ctx: StrategyContext) -> float:
        assert ctx.origin_price is not None, "進場前必須先設定 origin_price"
        return compute_short_entry_price(ctx.origin_price, self.deviation_pct)
