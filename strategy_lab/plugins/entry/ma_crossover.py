"""MA-crossover strategy's entry: buy when the fast SMA crosses above the
slow SMA. Needs a price *history*, not a scalar reference — this is the
point of divergence from deviation_from_reference.py's entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register


def simple_moving_average(values: Sequence[float], window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


@register("entry", "ma_crossover")
@dataclass
class MACrossoverEntry:
    fast_window: int = 5
    slow_window: int = 20

    def should_enter(self, ctx: StrategyContext) -> bool:
        history = ctx.price_history
        if len(history) < self.slow_window + 1:
            return False
        fast_now = simple_moving_average(history, self.fast_window)
        slow_now = simple_moving_average(history, self.slow_window)
        fast_prev = simple_moving_average(history[:-1], self.fast_window)
        slow_prev = simple_moving_average(history[:-1], self.slow_window)
        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return False
        return fast_prev <= slow_prev and fast_now > slow_now

    def entry_price(self, ctx: StrategyContext) -> float:
        return ctx.price
