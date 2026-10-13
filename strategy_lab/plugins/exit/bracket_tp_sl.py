"""MA 均線交叉策略的出場邏輯:停利 OR 停損,兩者任一觸發即出場。這是
「用 Or 組合兩個獨立條件」的示範案例——條件樹在 __post_init__ 組成,
這個類別只保留「觸發後出場價要算多少」。"""

from __future__ import annotations

from dataclasses import dataclass, field

from strategy_lab.interfaces import PlannedExit, StrategyContext
from strategy_lab.registry import register
from strategy_lab.rules.base import Condition
from strategy_lab.rules.composite import ByDirection, Or
from strategy_lab.rules.conditions import PriceChangeFromEntry


@register("exit", "bracket_tp_sl")
@dataclass
class BracketTPSLExit:
    take_profit_pct: float
    stop_loss_pct: float
    rule: Condition = field(init=False)

    def __post_init__(self) -> None:
        # 做空是鏡像:跌 take_profit_pct% 停利、漲 stop_loss_pct% 停損(2026-10-12)
        self.rule = ByDirection(
            Or(PriceChangeFromEntry(self.take_profit_pct, "up"), PriceChangeFromEntry(self.stop_loss_pct, "down")),
            Or(PriceChangeFromEntry(self.take_profit_pct, "down"), PriceChangeFromEntry(self.stop_loss_pct, "up")),
        )

    def exit_price(self, ctx: StrategyContext) -> float:
        return ctx.price

    def planned_exit(self, ctx: StrategyContext) -> PlannedExit:
        # 條件樹是 PriceChangeFromEntry up/down,只描述做多:止盈在上、停損在下。
        entry = ctx.entry_price if ctx.entry_price is not None else ctx.price
        sign = -1 if ctx.direction == "short" else 1
        return PlannedExit(
            take_profit=entry * (1 + sign * self.take_profit_pct / 100),
            stop_loss=entry * (1 - sign * self.stop_loss_pct / 100),
        )
