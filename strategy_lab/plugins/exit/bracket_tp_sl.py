"""MA-crossover strategy's exit: take-profit OR stop-loss relative to the
filled entry price. Unlike return_to_reference, the exit price is *relative
to entry*, not a fixed target, and the trigger is checked every tick rather
than resting entirely on a limit order's price."""

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
        assert ctx.entry_price is not None, "entry_price must be set once in position"
        change_pct = (ctx.price - ctx.entry_price) / ctx.entry_price * 100
        return change_pct >= self.take_profit_pct or change_pct <= -self.stop_loss_pct

    def exit_price(self, ctx: StrategyContext) -> float:
        return ctx.price
