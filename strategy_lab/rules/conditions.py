"""
規則引擎的具體條件(primitives)。每一個都只依賴 StrategyContext,不知道
自己會被哪個 plugin 使用、組成什麼樣的條件樹。

`simple_moving_average` 放在這裡(而不是 plugins/entry/ma_crossover.py)
是刻意的:如果放在 plugin 檔案裡,`MovingAverageCross` 要匯入它就會
形成循環匯入(plugin 匯入 rules,rules 又匯入 plugin)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Optional, Sequence

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
