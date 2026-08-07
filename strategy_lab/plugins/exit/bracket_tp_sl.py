"""MA 均線交叉策略的出場邏輯:停利 OR 停損,兩者任一觸發即出場。這是
「用 Or 組合兩個獨立條件」的示範案例——條件樹在 __post_init__ 組成,
這個類別只保留「觸發後出場價要算多少」。"""

from __future__ import annotations

from dataclasses import dataclass, field

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.composite import Or
from strategy_lab.rules.conditions import PriceChangeFromEntry


@register("exit", "bracket_tp_sl")
@dataclass
class BracketTPSLExit:
    take_profit_pct: float
    stop_loss_pct: float
    rule: Condition = field(init=False)

    def __post_init__(self) -> None:
        self.rule = Or(
            PriceChangeFromEntry(self.take_profit_pct, "up"),
            PriceChangeFromEntry(self.stop_loss_pct, "down"),
        )

    def exit_price(self, ctx: StrategyContext) -> float:
        return ctx.price
