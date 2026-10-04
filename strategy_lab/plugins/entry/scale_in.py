"""分注買入法的建倉 plugin:只決定「每注多少數量」,不決定價格。

每注的建倉價由使用者在 live_execution_config.yaml 的 `entry_prices` 逐一輸入
(不一定是越跌越買,價格完全由使用者決定),見 engine/scale_in_runner.py。

數量:第一注 = position_sizing 算出的數量 q1,第 k 注 = q1 × weights[k] / weights[0]。
例:weights [2, 3, 1]、q1 = 0.002 → 0.002 / 0.003 / 0.001。
平倉數量跟建倉一模一樣(每注平掉自己那一注),所以沒有另外的平倉比重。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, List

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.conditions import AlwaysTrue


@register("entry", "scale_in")
@dataclass
class ScaleInEntry:
    weights: List[float]
    rule: Condition = field(init=False)
    resting: ClassVar[bool] = True  # 一啟動就掛單 → StrategyRunner 拒絕 order_type=market
    scale_in: ClassVar[bool] = True  # 策略 YAML 的 scale_in 必須跟這個一致(dsl/loader.py 檢查)

    def __post_init__(self) -> None:
        if not self.weights or any(w <= 0 for w in self.weights):
            raise ValueError(f"weights 必須是至少一個、全部大於 0 的數字,目前是 {self.weights}")
        self.rule = AlwaysTrue()

    @property
    def lots(self) -> int:
        return len(self.weights)

    def lot_qtys(self, first_qty: float) -> List[float]:
        return [first_qty * w / self.weights[0] for w in self.weights]

    def entry_price(self, ctx: StrategyContext) -> float:
        raise NotImplementedError("分注策略的建倉價來自 entry_prices,由 ScaleInRunner 處理")
