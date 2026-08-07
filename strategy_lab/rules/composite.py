"""
可組合的邏輯運算子。每一個都只依賴 Condition 合約
(rules/base.py 的 evaluate(ctx) -> bool),不需要知道被組合的是哪一種
具體條件——這就是條件樹可以任意疊代巢狀的原因。
"""

from __future__ import annotations

from strategy_lab.interfaces import StrategyContext
from strategy_lab.rules.base import Condition


class And:
    def __init__(self, *conditions: Condition) -> None:
        self.conditions = conditions

    def evaluate(self, ctx: StrategyContext) -> bool:
        return all(c.evaluate(ctx) for c in self.conditions)


class Or:
    def __init__(self, *conditions: Condition) -> None:
        self.conditions = conditions

    def evaluate(self, ctx: StrategyContext) -> bool:
        return any(c.evaluate(ctx) for c in self.conditions)


class Not:
    def __init__(self, condition: Condition) -> None:
        self.condition = condition

    def evaluate(self, ctx: StrategyContext) -> bool:
        return not self.condition.evaluate(ctx)
