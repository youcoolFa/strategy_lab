"""Phase 2 起,exit plugin 只負責:(1) 在 __post_init__ 組出正確的
Condition,(2) 計算 exit_price。真正的觸發邏輯真值表測試在
tests/test_rules_conditions.py、組合邏輯測試在
tests/test_rules_composite.py。"""

from datetime import datetime, timezone

from strategy_lab.interfaces import StrategyContext
from strategy_lab.plugins.exit.bracket_tp_sl import BracketTPSLExit
from strategy_lab.plugins.exit.max_hold_duration import MaxHoldDurationExit
from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit
from strategy_lab.rules.composite import Or
from strategy_lab.rules.conditions import MaxDurationElapsed, PriceAtOrAboveReference, PriceChangeFromEntry


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
        assert isinstance(plugin.rule, PriceAtOrAboveReference)

    def test_exit_price_is_origin_price(self):
        plugin = ReturnToReferenceExit()
        ctx = make_ctx(price=1200.0, origin_price=1000.0)
        assert plugin.exit_price(ctx) == 1000.0


class TestBracketTPSLExit:
    def test_rule_is_or_of_take_profit_and_stop_loss(self):
        plugin = BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5)
        assert isinstance(plugin.rule, Or)
        tp, sl = plugin.rule.conditions
        assert isinstance(tp, PriceChangeFromEntry) and (tp.threshold_pct, tp.direction) == (1.0, "up")
        assert isinstance(sl, PriceChangeFromEntry) and (sl.threshold_pct, sl.direction) == (0.5, "down")

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
