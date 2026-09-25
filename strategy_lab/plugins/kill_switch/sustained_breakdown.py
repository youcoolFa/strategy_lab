"""SustainedBreakoutKillSwitch 的鏡像,給 direction: short 策略用——
連續向下突破觸發,不是向上。見 sustained_breakout.py 的說明,機制完全
對稱,差別只在底層 Condition 換成 SustainedPriceBreakdown。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import SustainedPriceBreakdown


@register("kill_switch", "sustained_breakdown")
@dataclass
class SustainedBreakdownKillSwitch:
    threshold_price: float
    reference_price: float
    hours: Optional[float] = None
    minutes: Optional[float] = None
    days: Optional[float] = None
    margin_pct: Optional[float] = None
    margin_fixed: Optional[float] = None
    rule: Condition = field(init=False)

    def __post_init__(self) -> None:
        self.rule = SustainedPriceBreakdown(
            threshold_price=self.threshold_price,
            reference_price=self.reference_price,
            hours=self.hours,
            minutes=self.minutes,
            days=self.days,
            margin_pct=self.margin_pct,
            margin_fixed=self.margin_fixed,
        )
        self.hours = self.rule.hours  # 正規化,跟 SustainedBreakdownKillSwitch 一致

    @property
    def window_duration(self) -> timedelta:
        return self.rule.window_duration
