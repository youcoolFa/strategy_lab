"""週末均值回歸策略的進場邏輯:當價格跌破「窗口開始時就固定住的參考價」
(sat_strategy 的 origin_price)deviation_pct% 時買進。
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register


def compute_entry_price(reference_price: float, deviation_pct: float) -> float:
    """原封不動移植自 sat_strategy/app/bot.py 的 compute_entry_price()。"""
    return round(reference_price * (1 - deviation_pct / 100), 2)


@register("entry", "deviation_from_reference")
@dataclass
class DeviationFromReferenceEntry:
    deviation_pct: float

    def should_enter(self, ctx: StrategyContext) -> bool:
        # 永遠嘗試進場——跟 bot.py 一樣,價格觸發條件是藏在掛出去的限價單
        # 價位裡,不是在這裡先做一次判斷。
        return True

    def entry_price(self, ctx: StrategyContext) -> float:
        assert ctx.origin_price is not None, "進場前必須先設定 origin_price"
        return compute_entry_price(ctx.origin_price, self.deviation_pct)
