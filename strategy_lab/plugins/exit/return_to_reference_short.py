"""ReturnToReferenceExit 的鏡像版本——給平空倉用:回到 origin_price 就
回補平倉。「什麼時候觸發」交給 rules.conditions.PriceAtOrBelowReference,
這個類別只保留「觸發後出場價要算多少」——跟 ReturnToReferenceExit
完全對稱的分工,見 docs/ARCHITECTURE.md §6.11。"""

from __future__ import annotations

from dataclasses import dataclass

from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit
from strategy_lab.registry import register


@register("exit", "return_to_reference_short")
@dataclass
class ShortReturnToReferenceExit(ReturnToReferenceExit):
    """舊名,行為跟 return_to_reference 完全一樣:方向看 ctx.direction(2026-10-12)。"""
