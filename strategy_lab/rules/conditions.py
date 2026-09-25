"""
規則引擎的具體條件(primitives)。每一個都只依賴 StrategyContext,不知道
自己會被哪個 plugin 使用、組成什麼樣的條件樹。

`simple_moving_average` 放在這裡(而不是 plugins/entry/ma_crossover.py)
是刻意的:如果放在 plugin 檔案裡,`MovingAverageCross` 要匯入它就會
形成循環匯入(plugin 匯入 rules,rules 又匯入 plugin)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Literal, Optional, Sequence, Tuple

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
class PriceAboveReference:
    """PriceBelowReference 的鏡像版本,給開空倉用:現價漲破 origin_price
    的 deviation_pct% 就觸發。"""

    deviation_pct: float

    def evaluate(self, ctx: StrategyContext) -> bool:
        assert ctx.origin_price is not None, "origin_price 必須已設定"
        target = ctx.origin_price * (1 + self.deviation_pct / 100)
        return ctx.price >= target


@dataclass
class PriceAtOrBelowReference:
    """PriceAtOrAboveReference 的鏡像版本,給平空倉用:現價回到(或跌破)
    origin_price 就觸發。"""

    def evaluate(self, ctx: StrategyContext) -> bool:
        assert ctx.origin_price is not None, "origin_price 必須已設定"
        return ctx.price <= ctx.origin_price


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
    """過去連續 `hours` 個小時內,每一筆觀察到的價格都超過
    threshold_price,且這段期間的平均價格超過 reference_price 加上
    margin_pct%(或 margin_fixed 的絕對值)——用來當終止整套策略迴圈的
    kill switch 觸發條件,不是進出場條件。

    用「連續小時數」而不是「連續日曆日」(舊版設計):日曆日版本需要跨過
    日期邊界才會「結算」一天,若 `TimeWindow` 本身的時間窗撐不過
    N+1 個日曆日,kill switch 實質上永遠不會觸發——`WeeklyWindow` 預設
    只有約 50 小時,連續 3 個日曆日永遠撐不到,這是實際遇到的問題,
    不是理論上的顧慮(見 docs/ARCHITECTURE.md)。改成小時為單位的滾動
    視窗後,不再有這種「日曆日邊界」造成的隱性下限。

    跟本檔案其他 condition 不同:這個物件會在 evaluate() 呼叫之間累積
    內部狀態(記錄每次觀察到的 (時間, 價格)),因為
    StrategyContext.price_history 本身沒有帶時間戳記,無法從外部反推
    每一筆的觀察時間——這個限制記錄在 docs/ARCHITECTURE.md。

    `hours`/`minutes`/`days` 三者互斥,恰好給一個(不給則預設
    `hours=72.0`),`__post_init__` 換算後統一正規化回 `self.hours`——
    只是換算輸入單位的方便寫法,內部只有一種表示。刻意不支援
    `months`/`years`:日曆月、年的長度不固定,直接支援會重新引入「日曆
    邊界」那類問題(見 docs/ARCHITECTURE.md §4.6.1)。
    """

    threshold_price: float
    reference_price: float
    hours: Optional[float] = None
    minutes: Optional[float] = None
    days: Optional[float] = None
    margin_pct: Optional[float] = None
    margin_fixed: Optional[float] = None

    _first_observed_at: Optional[datetime] = field(default=None, init=False, repr=False)
    _history: List[Tuple[datetime, float]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if (self.margin_pct is None) == (self.margin_fixed is None):
            raise ValueError("must set exactly one of margin_pct / margin_fixed")

        given = [v for v in (self.hours, self.minutes, self.days) if v is not None]
        if len(given) > 1:
            raise ValueError("must set at most one of hours / minutes / days")
        if self.minutes is not None:
            self.hours = self.minutes / 60
        elif self.days is not None:
            self.hours = self.days * 24
        elif self.hours is None:
            self.hours = 72.0
        # 正規化後 self.hours 永遠是解析完的小時數(不管原本用哪個單位
        # 輸入),evaluate()/_observe() 之後完全不用管 minutes/days。

    @property
    def window_duration(self) -> timedelta:
        return timedelta(hours=self.hours)

    def evaluate(self, ctx: StrategyContext) -> bool:
        self._observe(ctx)

        assert self._first_observed_at is not None
        if ctx.now - self._first_observed_at < timedelta(hours=self.hours):
            return False  # 觀察時間還沒涵蓋完整的 hours 視窗

        if not all(price > self.threshold_price for _, price in self._history):
            return False

        avg = sum(price for _, price in self._history) / len(self._history)
        target = (
            self.reference_price * (1 + self.margin_pct / 100)
            if self.margin_pct is not None
            else self.reference_price + self.margin_fixed
        )
        return avg > target

    def _observe(self, ctx: StrategyContext) -> None:
        if self._first_observed_at is None:
            self._first_observed_at = ctx.now
        self._history.append((ctx.now, ctx.price))

        cutoff = ctx.now - timedelta(hours=self.hours)
        self._history = [(t, price) for (t, price) in self._history if t >= cutoff]
