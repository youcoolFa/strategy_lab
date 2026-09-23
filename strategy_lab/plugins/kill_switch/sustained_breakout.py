"""終止整個策略迴圈的 kill switch:跟 TimeWindow(純時間排程)是兩個
獨立的「什麼時候該收攤」合約,這個是因為市場行為(價格連續多日突破)
而收攤,不是排程時間到了。觸發後 runner 呼叫 `_cleanup()`,行為跟窗口
到期完全一樣(平倉 + 取消未成交單),只是觸發條件換成
SustainedPriceBreakout。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import SustainedPriceBreakout


@register("kill_switch", "sustained_breakout")
@dataclass
class SustainedBreakoutKillSwitch:
    threshold_price: float
    reference_price: float
    days: int = 3
    margin_pct: Optional[float] = None
    margin_fixed: Optional[float] = None
    rule: Condition = field(init=False)

    def __post_init__(self) -> None:
        self.rule = SustainedPriceBreakout(
            threshold_price=self.threshold_price,
            reference_price=self.reference_price,
            days=self.days,
            margin_pct=self.margin_pct,
            margin_fixed=self.margin_fixed,
        )
