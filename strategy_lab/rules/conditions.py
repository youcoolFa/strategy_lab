"""
規則引擎的具體條件(primitives)。每一個都只依賴 StrategyContext,不知道
自己會被哪個 plugin 使用、組成什麼樣的條件樹。

`simple_moving_average` 放在這裡(而不是 plugins/entry/ma_crossover.py)
是刻意的:如果放在 plugin 檔案裡,`MovingAverageCross` 要匯入它就會
形成循環匯入(plugin 匯入 rules,rules 又匯入 plugin)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Literal, Optional, Sequence

from strategy_lab.interfaces import StrategyContext


def simple_moving_average(values: Sequence[float], window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


@dataclass
class PriceBelowReference:
    """現價跌破 origin_price 的 deviation_pct%。"""

    deviation_pct: float

    def evaluate(self, ctx: StrategyContext) -> bool:
        assert ctx.origin_price is not None, "origin_price 必須已設定"
        target = ctx.origin_price * (1 - self.deviation_pct / 100)
        return ctx.price <= target


@dataclass
class PriceAtOrAboveReference:
    """現價回到(或高於)origin_price。"""

    def evaluate(self, ctx: StrategyContext) -> bool:
        assert ctx.origin_price is not None, "origin_price 必須已設定"
        return ctx.price >= ctx.origin_price


@dataclass
class MovingAverageCross:
    """快線由下往上穿越慢線,剛發生的那一刻(不含已經穿越過、持續在上方的情況)。"""

    fast_window: int
    slow_window: int

    def evaluate(self, ctx: StrategyContext) -> bool:
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


@dataclass
class PriceChangeFromEntry:
    """相對進場價的漲跌幅是否已達門檻。
    direction="up" 用於停利,"down" 用於停損。"""

    threshold_pct: float
    direction: Literal["up", "down"]

    def evaluate(self, ctx: StrategyContext) -> bool:
        assert ctx.entry_price is not None, "entry_price 必須已設定"
        change_pct = (ctx.price - ctx.entry_price) / ctx.entry_price * 100
        if self.direction == "up":
            return change_pct >= self.threshold_pct
        return change_pct <= -self.threshold_pct


@dataclass
class MaxDurationElapsed:
    """距離進場時間是否已超過 max_minutes 分鐘。"""

    max_minutes: int

    def evaluate(self, ctx: StrategyContext) -> bool:
        if ctx.entry_time is None:
            return False
        return ctx.now - ctx.entry_time >= timedelta(minutes=self.max_minutes)


@dataclass
class SustainedPriceBreakout:
    """連續 `days` 個「已經結束」的日曆日,每日收盤價都超過
    threshold_price,且這幾天的平均收盤價超過 reference_price 加上
    margin_pct%(或 margin_fixed 的絕對值)——用來當終止整套策略迴圈的
    kill switch 觸發條件,不是進出場條件。

    跟本檔案其他 condition 不同:這個物件會在 evaluate() 呼叫之間累積
    內部狀態(逐 tick 捲出每日收盤),因為 StrategyContext.price_history
    本身沒有帶時間戳記,無法從外部反推「哪幾筆屬於同一天」——這個限制
    記錄在 docs/ARCHITECTURE.md。「今天」尚未結束的部分不計入,只用已經
    跨過日期邊界、確定收盤的日子。
    """

    threshold_price: float
    reference_price: float
    days: int = 3
    margin_pct: Optional[float] = None
    margin_fixed: Optional[float] = None

    _current_date: Optional[date] = field(default=None, init=False, repr=False)
    _current_close: Optional[float] = field(default=None, init=False, repr=False)
    _finalized_closes: List[float] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if (self.margin_pct is None) == (self.margin_fixed is None):
            raise ValueError("must set exactly one of margin_pct / margin_fixed")

    def evaluate(self, ctx: StrategyContext) -> bool:
        self._observe(ctx)
        if len(self._finalized_closes) < self.days:
            return False
        recent = self._finalized_closes[-self.days :]
        if not all(close > self.threshold_price for close in recent):
            return False
        avg = sum(recent) / len(recent)
        target = (
            self.reference_price * (1 + self.margin_pct / 100)
            if self.margin_pct is not None
            else self.reference_price + self.margin_fixed
        )
        return avg > target

    def _observe(self, ctx: StrategyContext) -> None:
        today = ctx.now.date()
        if self._current_date is not None and today != self._current_date:
            self._finalized_closes.append(self._current_close)
        self._current_date = today
        self._current_close = ctx.price
