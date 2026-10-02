from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    equity: float
    maker_fee_rate: float
    taker_fee_rate: float
    leverage: Optional[float]
    margin_mode: Optional[str]
    now: datetime
    funding_interval_minutes: int = 480  # Bybit 永續預設每 8 小時結算一次資金費


@dataclass
class OrderPlan:
    strategy_name: str
    direction: str
    origin_price: float
    origin_source: str
    qty: float
    entry_price: float
    entry_known_in_advance: bool  # 一啟動就掛單的 plugin 才事先知道;依訊號進場的以現價估
    entry_crosses_market: bool  # 進場限價已經越過現價 → 一掛出去就吃單成交
    take_profit: Optional[float]
    stop_loss: Optional[float]
    exit_resting: bool  # 平倉單是先掛著等(maker),否則當成吃單(taker)
    cleanup_at: datetime
    loop: Optional[int] = None  # 重複次數;總 event 數 = loop + 1;None = 不限

    @property
    def notional(self) -> float:
        return self.entry_price * self.qty


@dataclass
class Estimate:
    plan: OrderPlan
    market: MarketSnapshot

    def entry_fee(self) -> float:
        rate = self.market.taker_fee_rate if self.plan.entry_crosses_market else self.market.maker_fee_rate
        return self.plan.notional * rate

    def exit_fee(self, price: float, resting: bool) -> float:
        rate = self.market.maker_fee_rate if resting else self.market.taker_fee_rate
        return price * self.plan.qty * rate


@dataclass
class Row:
    key: str
    label: str
    text: str
    value: Optional[float] = None
    warning: bool = False


@dataclass
class MetricResult:
    title: str
    rows: List[Row] = field(default_factory=list)
