"""weekend_band_reversion 的平倉機制:跟 resting_return_to_reference 一樣,
進場一成交就馬上掛 reduceOnly 限價平倉單,差別在平倉點不是 origin,而是
往獲利方向再偏 offset_pct%:
    long  -> 賣出平倉在 origin × (1 + offset_pct%)
    short -> 買回平倉在 origin × (1 − offset_pct%)
offset_pct = 0 等同 resting_return_to_reference。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from strategy_lab.interfaces import PlannedExit, StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import AlwaysTrue


@register("exit", "resting_offset_from_reference")
@dataclass
class RestingOffsetFromReferenceExit:
    offset_pct: float
    rule: Condition = field(init=False)
    resting: ClassVar[bool] = True

    def __post_init__(self) -> None:
        self.rule = AlwaysTrue()

    def exit_price(self, ctx: StrategyContext) -> float:
        assert ctx.origin_price is not None, "出場前必須先設定 origin_price"
        if ctx.direction == "short":
            return ctx.origin_price * (1 - self.offset_pct / 100)
        return ctx.origin_price * (1 + self.offset_pct / 100)

    def planned_exit(self, ctx: StrategyContext) -> PlannedExit:
        return PlannedExit(take_profit=self.exit_price(ctx), stop_loss=None)
