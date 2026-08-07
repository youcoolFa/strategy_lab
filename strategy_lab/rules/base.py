"""
規則引擎的最小合約:一個 Condition 只需要能回答「現在條件成立嗎」。
`rules/composite.py` 的 And/Or/Not 只依賴這個合約,不需要知道被組合的
是哪一種具體條件——這就是條件樹可以任意疊代巢狀的原因。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from strategy_lab.interfaces import StrategyContext


@runtime_checkable
class Condition(Protocol):
    def evaluate(self, ctx: StrategyContext) -> bool: ...
