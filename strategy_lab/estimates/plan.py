"""把策略的 plugin 跟市場資料組成 OrderPlan。價格都問 plugin 自己,這裡
不重複任何進出場公式。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from strategy_lab.estimates.model import LevelPlan, MarketSnapshot, OrderPlan
from strategy_lab.interfaces import EntrySignal, ExitSignal, PlannedExit, StrategyContext
from strategy_lab.live.instrument_limits import InstrumentLimits, fix_price


def _round(price: Optional[float], limits: Optional[InstrumentLimits], side: str) -> Optional[float]:
    if price is None or limits is None:
        return price
    return fix_price(price, limits, side=side)


def _crosses(entry_price: float, market_price: float, direction: str) -> bool:
    if direction == "short":
        return entry_price <= market_price
    return entry_price >= market_price


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
    crosses = _crosses(entry_price, market.price, direction)

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


def build_scale_in_plan(
    strategy_name: str,
    entry: EntrySignal,
    exit: ExitSignal,
    direction: str,
    entry_prices: List[float],
    qtys: List[float],
    market: MarketSnapshot,
    cleanup_at: datetime,
    limits: Optional[InstrumentLimits] = None,
    loop: Optional[int] = None,
) -> OrderPlan:
    """分注策略:每注建倉價來自 entry_prices,平倉價問 exit plugin(建倉價 ± 距離)。
    頂層的 entry_price/qty/take_profit 填第一注,讓只看單一價位的地方仍然能用。"""
    entry_side, exit_side = ("Sell", "Buy") if direction == "short" else ("Buy", "Sell")
    levels = []
    for i, (raw_price, qty) in enumerate(zip(entry_prices, qtys)):
        if raw_price <= 0:
            continue  # 建倉價 0 = 沒有這一注
        price = _round(raw_price, limits, entry_side)
        take_profit = _round(exit.exit_price_for(price, direction), limits, exit_side)
        levels.append(LevelPlan(i + 1, price, qty, take_profit, _crosses(price, market.price, direction)))

    first = levels[0]
    return OrderPlan(
        strategy_name=strategy_name,
        direction=direction,
        origin_price=None,
        origin_source="建倉價來自 entry_prices",
        qty=first.qty,
        entry_price=first.entry_price,
        entry_known_in_advance=True,
        entry_crosses_market=any(l.crosses_market for l in levels),
        take_profit=first.take_profit,
        stop_loss=None,
        exit_resting=True,
        cleanup_at=cleanup_at,
        loop=loop,
        levels=levels,
    )


def build_band_plan(
    strategy_name: str,
    entry: Any,
    origin_price: float,
    origin_source: str,
    qty: float,
    market: MarketSnapshot,
    cleanup_at: datetime,
    limits: Optional[InstrumentLimits] = None,
) -> OrderPlan:
    """區間策略(2026-10-10):一開始上下各掛一張,成交一張就在對面補一張,持倉在 ±qty 之間切換。
    買價放 entry_price、賣價放 take_profit 與 band_sell_price,direction 用 long:每輪損益照
    買價 → 賣價 算(空單 賣價 → 買價 的價差一樣)。現價已經在區間外 → 有一張一掛就吃單成交。"""
    raw_buy, raw_sell = entry.prices(origin_price)
    buy = _round(raw_buy, limits, "Buy")
    sell = _round(raw_sell, limits, "Sell")
    return OrderPlan(
        strategy_name=strategy_name,
        direction="long",
        origin_price=origin_price,
        origin_source=origin_source,
        qty=qty,
        entry_price=buy,
        entry_known_in_advance=True,
        entry_crosses_market=market.price <= buy or market.price >= sell,
        take_profit=sell,
        stop_loss=None,
        exit_resting=True,
        cleanup_at=cleanup_at,
        loop=None,
        band_sell_price=sell,
    )
