"""sat_strategy/app/bot.py 的進場機制:一啟動就掛限價單在
origin_price 偏離 deviation_pct% 的位置,由交易所撮合決定何時成交,
不先等價格越過門檻才下單(那是 deviation_from_reference 的做法)。

方向從 ctx.direction 讀(策略 YAML 的 direction):
    long  -> 掛買單在 origin × (1 − deviation_pct%)
    short -> 掛賣單在 origin × (1 + deviation_pct%)

跟 sat_strategy 唯一刻意不同的地方:不 round(..., 2)。那是為 BTC 價位
寫的,套在 WLD(約 0.48)上會把 0.75% 扭成 -1.5% 或 +0.2%。精度交給
LiveBroker 依交易所真實 tickSize 修正(見 live/instrument_limits.py)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import AlwaysTrue


def compute_resting_entry_price(origin_price: float, deviation_pct: float, direction: str) -> float:
    if direction == "short":
        return origin_price * (1 + deviation_pct / 100)
    return origin_price * (1 - deviation_pct / 100)


@register("entry", "resting_deviation_from_reference")
@dataclass
class RestingDeviationFromReferenceEntry:
    deviation_pct: float
    rule: Condition = field(init=False)
    resting: ClassVar[bool] = True  # StrategyRunner 據此拒絕 order_type=market

    def __post_init__(self) -> None:
        self.rule = AlwaysTrue()

    def entry_price(self, ctx: StrategyContext) -> float:
        assert ctx.origin_price is not None, "進場前必須先設定 origin_price"
        return compute_resting_entry_price(ctx.origin_price, self.deviation_pct, ctx.direction)
