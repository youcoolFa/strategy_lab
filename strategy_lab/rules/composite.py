"""
可組合的邏輯運算子。每一個都只依賴 Condition 合約
(rules/base.py 的 evaluate(ctx) -> bool),不需要知道被組合的是哪一種
具體條件——這就是條件樹可以任意疊代巢狀的原因。

`__eq__`/`__repr__` 是手寫的,不是 `@dataclass`——因為建構子吃的是
`*conditions`(可變數量的位置參數),`@dataclass` 沒辦法直接表達這種
形狀。手寫 `__eq__` 讓兩個內容一樣的條件樹可以 `==`(而不是退化成用
記憶體位址比較),Phase 3 的 DSL regression test(比較 YAML 組出來的
rule 跟手動組出來的版本)需要依賴這個行為。
"""

from __future__ import annotations

from strategy_lab.interfaces import StrategyContext
from strategy_lab.rules.base import Condition


class And:
    def __init__(self, *conditions: Condition) -> None:
        self.conditions = conditions

    def evaluate(self, ctx: StrategyContext) -> bool:
        return all(c.evaluate(ctx) for c in self.conditions)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, And) and self.conditions == other.conditions

    def __repr__(self) -> str:
        return f"And{self.conditions!r}"


class Or:
    def __init__(self, *conditions: Condition) -> None:
        self.conditions = conditions

    def evaluate(self, ctx: StrategyContext) -> bool:
        return any(c.evaluate(ctx) for c in self.conditions)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Or) and self.conditions == other.conditions

    def __repr__(self) -> str:
        return f"Or{self.conditions!r}"


class Not:
    def __init__(self, condition: Condition) -> None:
        self.condition = condition

    def evaluate(self, ctx: StrategyContext) -> bool:
        return not self.condition.evaluate(ctx)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Not) and self.condition == other.condition

    def __repr__(self) -> str:
        return f"Not({self.condition!r})"
