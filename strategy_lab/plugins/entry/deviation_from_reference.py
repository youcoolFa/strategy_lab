"""Weekend mean-reversion's entry: buy once price is deviation_pct% below a
fixed reference price captured at window start (sat_strategy's origin_price).
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy_lab.interfaces import StrategyContext
from strategy_lab.registry import register


def compute_entry_price(reference_price: float, deviation_pct: float) -> float:
    """Ported verbatim from sat_strategy/app/bot.py's compute_entry_price()."""
    return round(reference_price * (1 - deviation_pct / 100), 2)


@register("entry", "deviation_from_reference")
@dataclass
class DeviationFromReferenceEntry:
    deviation_pct: float

    def should_enter(self, ctx: StrategyContext) -> bool:
        # Always attempt entry — like bot.py, the price trigger lives in the
        # resting limit order's price, not in a pre-check here.
        return True

    def entry_price(self, ctx: StrategyContext) -> float:
        assert ctx.origin_price is not None, "origin_price must be set before entry"
        return compute_entry_price(ctx.origin_price, self.deviation_pct)
