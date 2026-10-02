"""把策略的 plugin 跟市場資料組成 OrderPlan。價格都問 plugin 自己,這裡
不重複任何進出場公式。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from strategy_lab.estimates.model import MarketSnapshot, OrderPlan
from strategy_lab.interfaces import EntrySignal, ExitSignal, PlannedExit, StrategyContext
from strategy_lab.live.instrument_limits import InstrumentLimits, fix_price


def _round(price: Optional[float], limits: Optional[InstrumentLimits], side: str) -> Optional[float]:
    if price is None or limits is None:
        return price
    return fix_price(price, limits, side=side)


def build_order_plan(
    strategy_name: str,
    entry: EntrySignal,
    exit: ExitSignal,
    direction: str,
    origin_price: float,
    origin_source: str,
    qty: float,
    market: MarketSnapshot,
    cleanup_at: datetime,
    limits: Optional[InstrumentLimits] = None,
    loop: Optional[int] = None,
) -> OrderPlan:
    entry_side, exit_side = ("Sell", "Buy") if direction == "short" else ("Buy", "Sell")

    ctx = StrategyContext(now=market.now, price=market.price, origin_price=origin_price, direction=direction)
    entry_price = _round(entry.entry_price(ctx), limits, entry_side)
    if direction == "short":
        crosses = entry_price <= market.price
    else:
        crosses = entry_price >= market.price

    exit_ctx = StrategyContext(
        now=market.now, price=market.price, origin_price=origin_price, entry_price=entry_price, direction=direction
    )
    planned = exit.planned_exit(exit_ctx) if hasattr(exit, "planned_exit") else PlannedExit(None, None)

    return OrderPlan(
        strategy_name=strategy_name,
        direction=direction,
        origin_price=origin_price,
        origin_source=origin_source,
        qty=qty,
        entry_price=entry_price,
        entry_known_in_advance=getattr(entry, "resting", False),
        entry_crosses_market=crosses,
        take_profit=_round(planned.take_profit, limits, exit_side),
        stop_loss=_round(planned.stop_loss, limits, exit_side),
        exit_resting=getattr(exit, "resting", False),
        cleanup_at=cleanup_at,
        loop=loop,
    )
