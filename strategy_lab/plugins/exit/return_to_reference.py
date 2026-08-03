"""Weekend mean-reversion's exit: close back at the same origin_price the
entry was measured from. Ported from sat_strategy/app/bot.py's
_place_exit_order(origin_price, ...)."""

from __future__ import annotations

from dataclasses import dataclass

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register


@register("exit", "return_to_reference")
@dataclass
class ReturnToReferenceExit:
    def should_exit(self, ctx: StrategyContext) -> bool:
        return True

    def exit_price(self, ctx: StrategyContext) -> float:
        assert ctx.origin_price is not None, "origin_price must be set before exit"
        return ctx.origin_price
