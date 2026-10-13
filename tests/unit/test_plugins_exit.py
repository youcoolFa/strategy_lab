"""Phase 2 起,exit plugin 只負責:(1) 在 __post_init__ 組出正確的
Condition,(2) 計算 exit_price。真正的觸發邏輯真值表測試在
tests/unit/test_rules_conditions.py、組合邏輯測試在
tests/unit/test_rules_composite.py。"""

from datetime import datetime, timezone

import pytest

from strategy_lab.interfaces import StrategyContext
from strategy_lab.plugins.exit.bracket_tp_sl import BracketTPSLExit
from strategy_lab.plugins.exit.max_hold_duration import MaxHoldDurationExit
from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit
from strategy_lab.plugins.exit.return_to_reference_short import ShortReturnToReferenceExit
from strategy_lab.rules.composite import ByDirection, Or
from strategy_lab.rules.conditions import (
    MaxDurationElapsed,
    PriceAtOrAboveReference,
    PriceAtOrBelowReference,
    PriceChangeFromEntry,
)


def make_ctx(**overrides):
    base = dict(
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        price=100.0,
        price_history=(),
        origin_price=None,
        entry_price=None,
        position_qty=0.0,
        entry_time=None,
    )
    base.update(overrides)
    return StrategyContext(**base)


class TestReturnToReferenceExit:
    def test_rule_is_price_at_or_above_reference(self):
        plugin = ReturnToReferenceExit()
        assert plugin.rule == ByDirection(PriceAtOrAboveReference(), PriceAtOrBelowReference())

    def test_exit_price_is_origin_price(self):
        plugin = ReturnToReferenceExit()
        ctx = make_ctx(price=1200.0, origin_price=1000.0)
        assert plugin.exit_price(ctx) == 1000.0


class TestShortReturnToReferenceExit:
    """ReturnToReferenceExit 的鏡像版本(平空倉用)——見
    docs/ARCHITECTURE.md §6.11。"""

    def test_rule_is_price_at_or_below_reference(self):
        """舊名,跟 return_to_reference 一樣,方向看 ctx.direction。"""
        plugin = ShortReturnToReferenceExit()
        assert plugin.rule == ByDirection(PriceAtOrAboveReference(), PriceAtOrBelowReference())
        assert plugin.rule.evaluate(make_ctx(price=990.0, origin_price=1000.0, direction="short"))
        assert not plugin.rule.evaluate(make_ctx(price=990.0, origin_price=1000.0))

    def test_exit_price_is_origin_price(self):
        plugin = ShortReturnToReferenceExit()
        ctx = make_ctx(price=800.0, origin_price=1000.0)
        assert plugin.exit_price(ctx) == 1000.0


class TestBracketTPSLExit:
    def test_rule_is_or_of_take_profit_and_stop_loss(self):
        plugin = BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5)
        assert plugin.rule == ByDirection(
            Or(PriceChangeFromEntry(1.0, "up"), PriceChangeFromEntry(0.5, "down")),
            Or(PriceChangeFromEntry(1.0, "down"), PriceChangeFromEntry(0.5, "up")),
        )

    def test_short_take_profit_is_below_and_stop_loss_above(self):
        plugin = BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5)
        short = dict(entry_price=1000.0, direction="short")
        assert plugin.rule.evaluate(make_ctx(price=990.0, **short))  # 跌 1% 停利
        assert plugin.rule.evaluate(make_ctx(price=1005.0, **short))  # 漲 0.5% 停損
        assert not plugin.rule.evaluate(make_ctx(price=1003.0, **short))
        planned = plugin.planned_exit(make_ctx(price=1000.0, **short))
        assert planned.take_profit == pytest.approx(990.0) and planned.stop_loss == pytest.approx(1005.0)

    def test_exit_price_is_current_price(self):
        plugin = BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5)
        assert plugin.exit_price(make_ctx(price=1010.0)) == 1010.0


class TestMaxHoldDurationExit:
    def test_rule_is_max_duration_elapsed(self):
        plugin = MaxHoldDurationExit(max_minutes=30)
        assert isinstance(plugin.rule, MaxDurationElapsed)
        assert plugin.rule.max_minutes == 30

    def test_exit_price_is_current_price(self):
        plugin = MaxHoldDurationExit(max_minutes=30)
        assert plugin.exit_price(make_ctx(price=1010.0)) == 1010.0
