"""區間策略的建倉 plugin(weekend_band_reversion,2026-10-10 改版):只決定兩個價位。

    買單 = origin × (1 − buy_pct%)    賣單 = origin × (1 + sell_pct%)

一啟動上下各掛一張;成交一張就在對面價位補一張同數量的單,持倉在 ±1 之間切換
(單向持倉:買賣單是淨部位的加減),直到時間窗結束收尾。下單與補單由 engine/band_runner.py 處理。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Tuple

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import AlwaysTrue


def _check_pct(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 100:
        raise ValueError(f"{name} 必須是 0 到 100 之間(不含)的數字,目前是 {value!r}")
    return float(value)


@register("entry", "band")
@dataclass
class BandEntry:
    buy_pct: float
    sell_pct: float
    rule: Condition = field(init=False)
    resting: ClassVar[bool] = True  # 一啟動就掛單 → 只能 order_type=limit
    scale_in: ClassVar[bool] = False
    band: ClassVar[bool] = True  # 策略 YAML 的 band 必須跟這個一致(dsl/loader.py 檢查)

    def __post_init__(self) -> None:
        self.buy_pct = _check_pct("buy_pct", self.buy_pct)
        self.sell_pct = _check_pct("sell_pct", self.sell_pct)
        self.rule = AlwaysTrue()

    def prices(self, origin: float) -> Tuple[float, float]:
        """(買價, 賣價)"""
        return origin * (1 - self.buy_pct / 100), origin * (1 + self.sell_pct / 100)

    def entry_price(self, ctx: StrategyContext) -> float:
        raise NotImplementedError("區間策略的價位由 BandRunner 用 prices(origin) 決定")
