"""週末均值回歸策略的進場邏輯:買進條件是價格跌破 origin_price 的
deviation_pct%。Phase 2 起,「什麼時候觸發」交給
rules.conditions.PriceBelowReference,這個類別只保留「觸發後掛單價要
算多少」。"""

from __future__ import annotations

from dataclasses import dataclass, field

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import PriceBelowReference


def compute_entry_price(reference_price: float, deviation_pct: float) -> float:
    """原封不動移植自 sat_strategy/app/bot.py 的 compute_entry_price()。"""
    return round(reference_price * (1 - deviation_pct / 100), 2)


@register("entry", "deviation_from_reference")
@dataclass
class DeviationFromReferenceEntry:
    deviation_pct: float
    rule: Condition = field(init=False)

    def __post_init__(self) -> None:
        self.rule = PriceBelowReference(self.deviation_pct)

    def entry_price(self, ctx: StrategyContext) -> float:
        assert ctx.origin_price is not None, "進場前必須先設定 origin_price"
        return compute_entry_price(ctx.origin_price, self.deviation_pct)
