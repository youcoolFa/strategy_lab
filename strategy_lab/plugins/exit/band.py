"""區間策略的平倉 plugin(weekend_band_reversion,2026-10-10 改版):只是標記,沒有參數。

區間策略的平倉就是對面那張補單(多單在賣價被平掉、同時翻成空單,反之亦然),
價位由 entry/band.py 決定,下單由 engine/band_runner.py 處理。策略 YAML 規定要有 exit,所以寫:
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
        raise NotImplementedError("區間策略沒有獨立的平倉價:對面那張補單就是平倉")
