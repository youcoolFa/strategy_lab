"""週末均值回歸策略的出場邏輯:回到跟進場時同一個 origin_price 就平倉。
移植自 sat_strategy/app/bot.py 的
_place_exit_order(origin_price, ...)。"""

from __future__ import annotations

from dataclasses import dataclass

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register


@register("exit", "return_to_reference")
@dataclass
class ReturnToReferenceExit:
    def should_exit(self, ctx: StrategyContext) -> bool:
        return True

    def exit_price(self, ctx: StrategyContext) -> float:
        assert ctx.origin_price is not None, "出場前必須先設定 origin_price"
        return ctx.origin_price
