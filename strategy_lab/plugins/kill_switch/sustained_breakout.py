"""終止整個策略迴圈的 kill switch:跟 TimeWindow(純時間排程)是兩個
獨立的「什麼時候該收攤」合約,這個是因為市場行為(價格連續多小時突破)
而收攤,不是排程時間到了。觸發後 runner 呼叫 `_cleanup()`,行為跟窗口
到期完全一樣(平倉 + 取消未成交單),只是觸發條件換成
SustainedPriceBreakout。

用「連續 hours 小時」而不是「連續日曆日」:`WeeklyWindow` 這種時間窗
通常撐不過好幾個完整日曆日,用日曆日當單位會讓 kill switch 實質上永遠
不會觸發(見 docs/ARCHITECTURE.md)。"""

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
    hours: float = 72.0
    margin_pct: Optional[float] = None
    margin_fixed: Optional[float] = None
    rule: Condition = field(init=False)

    def __post_init__(self) -> None:
        self.rule = SustainedPriceBreakout(
            threshold_price=self.threshold_price,
            reference_price=self.reference_price,
            hours=self.hours,
            margin_pct=self.margin_pct,
            margin_fixed=self.margin_fixed,
        )
