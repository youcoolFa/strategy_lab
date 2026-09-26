"""sat_strategy/app/bot.py 的出場機制:進場單一成交就馬上掛 reduceOnly
限價平倉單在 origin_price,不先等價格回到 origin 才下單。long/short 的
平倉價都是 origin,方向由 StrategyRunner 決定是賣出平多還是買回平空。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import AlwaysTrue


@register("exit", "resting_return_to_reference")
@dataclass
class RestingReturnToReferenceExit:
    rule: Condition = field(init=False)
    resting: ClassVar[bool] = True

    def __post_init__(self) -> None:
        self.rule = AlwaysTrue()

    def exit_price(self, ctx: StrategyContext) -> float:
        assert ctx.origin_price is not None, "出場前必須先設定 origin_price"
        return ctx.origin_price
