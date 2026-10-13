"""DeviationFromReferenceEntry 的鏡像版本——給開空倉用:賣出(做空)
條件是價格漲破 origin_price 的 deviation_pct%。「什麼時候觸發」交給
rules.conditions.PriceAboveReference,這個類別只保留「觸發後掛單價要
算多少」——跟 DeviationFromReferenceEntry 完全對稱的分工,見
docs/ARCHITECTURE.md §6.11。"""

from __future__ import annotations

from dataclasses import dataclass

from strategy_lab.plugins.entry.deviation_from_reference import (  # noqa: F401  compute_short_entry_price 舊匯入路徑
    DeviationFromReferenceEntry,
    compute_short_entry_price,
)
from strategy_lab.registry import register


@register("entry", "deviation_from_reference_short")
@dataclass
class ShortDeviationFromReferenceEntry(DeviationFromReferenceEntry):
    """舊名,行為跟 deviation_from_reference 完全一樣:方向看 ctx.direction(2026-10-12 起每個策略都能做多做空,
    weekend_short_* 的 YAML 照舊能用;設定檔把方向改成 long 就變成跌破買進)。"""
