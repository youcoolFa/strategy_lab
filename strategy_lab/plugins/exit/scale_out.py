"""分注買入法的平倉 plugin:每注成交後掛自己的 reduceOnly 平倉單,
平倉價 = 該注建倉價 ± 距離,數量 = 該注數量。

距離兩種單位:
    pct    -> 建倉價 × value%       (建倉 1000、1% → long 平倉 1010 / short 990)
    points -> 固定價差 value 點     (建倉 84000、500 點 → long 84500 / short 83500)

每注用自己的建倉價算,所以三注的平倉價各不相同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict

from strategy_lab.interfaces import PlannedExit, StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import AlwaysTrue

UNITS = ("pct", "points")


@register("exit", "scale_out")
@dataclass
class ScaleOutExit:
    distance: Dict[str, Any]
    rule: Condition = field(init=False)
    resting: ClassVar[bool] = True
    scale_in: ClassVar[bool] = True

    def __post_init__(self) -> None:
        value = self.distance.get("value") if isinstance(self.distance, dict) else None
        unit = self.distance.get("unit") if isinstance(self.distance, dict) else None
        if unit not in UNITS or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"distance 必須是 {{value: >0, unit: pct|points}},目前是 {self.distance}")
        self.rule = AlwaysTrue()

    @property
    def value(self) -> float:
        return float(self.distance["value"])

    @property
    def unit(self) -> str:
        return self.distance["unit"]

    def offset(self, entry_price: float) -> float:
        return entry_price * self.value / 100 if self.unit == "pct" else self.value

    def exit_price_for(self, entry_price: float, direction: str) -> float:
        if direction == "short":
            return entry_price - self.offset(entry_price)
        return entry_price + self.offset(entry_price)

    def exit_price(self, ctx: StrategyContext) -> float:
        assert ctx.entry_price is not None, "平倉前必須先有建倉價"
        return self.exit_price_for(ctx.entry_price, ctx.direction)

    def planned_exit(self, ctx: StrategyContext) -> PlannedExit:
        return PlannedExit(take_profit=self.exit_price(ctx), stop_loss=None)
