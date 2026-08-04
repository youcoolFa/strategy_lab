"""MA 均線交叉策略的出場邏輯:相對於成交進場價的停利(take-profit)或
停損(stop-loss),兩者任一觸發即出場。跟 return_to_reference 不同的
地方在於:出場價是「相對於進場價」,不是一個固定目標;而且觸發條件是
每個 tick 都在檢查,不是完全靠限價單掛在那裡等。"""

from __future__ import annotations

from dataclasses import dataclass

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register


@register("exit", "bracket_tp_sl")
@dataclass
class BracketTPSLExit:
    take_profit_pct: float
    stop_loss_pct: float

    def should_exit(self, ctx: StrategyContext) -> bool:
        assert ctx.entry_price is not None, "持倉期間必須已設定 entry_price"
        change_pct = (ctx.price - ctx.entry_price) / ctx.entry_price * 100
        return change_pct >= self.take_profit_pct or change_pct <= -self.stop_loss_pct

    def exit_price(self, ctx: StrategyContext) -> float:
        return ctx.price
