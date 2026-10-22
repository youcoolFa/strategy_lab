"""區間策略的平倉 plugin(sat_strategy,2026-10-10 改版):只是標記,沒有參數。

區間策略的平倉價 = origin(2026-10-17 改版):進場單成交後,由 engine/band_runner.py 立刻在 origin 掛反向的
reduce_only 限價單。策略 YAML 規定要有 exit,所以寫:
    exit:
      type: band
      params: {}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import AlwaysTrue


@register("exit", "band")
@dataclass
class BandExit:
    rule: Condition = field(init=False)
    resting: ClassVar[bool] = True
    scale_in: ClassVar[bool] = False
    band: ClassVar[bool] = True

    def __post_init__(self) -> None:
        self.rule = AlwaysTrue()

    def exit_price(self, ctx: StrategyContext) -> float:
        raise NotImplementedError("區間策略的平倉價就是 origin,由 BandRunner 直接掛單")
