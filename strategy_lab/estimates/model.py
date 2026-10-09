from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


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
class LevelPlan:
    """一注:建倉價、數量、平倉價。level k 對應第 k 注。"""

    index: int  # 1 起算
    entry_price: float
    qty: float
    take_profit: Optional[float]
    crosses_market: bool  # 建倉限價已經越過現價 → 一掛出去就吃單成交

    @property
    def notional(self) -> float:
        return self.entry_price * self.qty


@dataclass
class OrderPlan:
    strategy_name: str
    direction: str
    origin_price: Optional[float]  # 分注策略不用 → None
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
    # 分注策略每一注的計畫(build_scale_in_plan);非分注策略留空,lot_levels() 會把
    # 上面的 entry_price/qty/take_profit 當成唯一的 level 1。
    levels: List[LevelPlan] = field(default_factory=list)
    # 區間策略(build_band_plan,2026-10-10):賣單價位;買單價位放在 entry_price、賣價也放在 take_profit,
    # 所以「每輪損益」照多單 買價 → 賣價 算(空單 賣價 → 買價 的價差一樣)。非區間策略為 None。
    band_sell_price: Optional[float] = None

    @property
    def scale_in(self) -> bool:
        return bool(self.levels)

    @property
    def band(self) -> bool:
        return self.band_sell_price is not None

    def lot_levels(self) -> List[LevelPlan]:
        if self.levels:
            return self.levels
        return [LevelPlan(1, self.entry_price, self.qty, self.take_profit, self.entry_crosses_market)]

    @property
    def total_qty(self) -> float:
        return sum(l.qty for l in self.lot_levels())

    @property
    def notional(self) -> float:
        """全部注數都成交時的名義價值(最大部位)。"""
        return sum(l.notional for l in self.lot_levels())

    @property
    def avg_entry_price(self) -> float:
        return self.notional / self.total_qty


@dataclass
class Estimate:
    plan: OrderPlan
    market: MarketSnapshot

    def lot_entry_fee(self, level: LevelPlan) -> float:
        rate = self.market.taker_fee_rate if level.crosses_market else self.market.maker_fee_rate
        return level.notional * rate

    def lot_exit_fee(self, level: LevelPlan, price: float, resting: bool) -> float:
        rate = self.market.maker_fee_rate if resting else self.market.taker_fee_rate
        return price * level.qty * rate

    def entry_fee(self) -> float:
        """全部注數的建倉手續費。"""
        return sum(self.lot_entry_fee(l) for l in self.plan.lot_levels())

    def exit_fee(self, price: float, resting: bool) -> float:
        """全部注數都在同一個價格平倉的手續費(風險情境用)。"""
        return sum(self.lot_exit_fee(l, price, resting) for l in self.plan.lot_levels())


@dataclass
class Row:
    key: str
    label: str
    text: str
    value: Optional[float] = None
    warning: bool = False
    details: Dict[str, float] = field(default_factory=dict)  # 組成 value 的細項(例:毛利、手續費)


@dataclass
class MetricResult:
    title: str
    rows: List[Row] = field(default_factory=list)
