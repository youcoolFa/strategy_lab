"""Phase 2 起,entry plugin 只負責:(1) 在 __post_init__ 組出正確的
Condition,(2) 計算 entry_price。真正的觸發邏輯真值表測試在
tests/unit/test_rules_conditions.py。"""

from datetime import datetime, timezone

from strategy_lab.interfaces import StrategyContext
from strategy_lab.plugins.entry.deviation_from_reference import DeviationFromReferenceEntry, compute_entry_price
from strategy_lab.plugins.entry.deviation_from_reference_short import (
    ShortDeviationFromReferenceEntry,
    compute_short_entry_price,
)
from strategy_lab.plugins.entry.ma_crossover import MACrossoverEntry
from strategy_lab.rules.conditions import MovingAverageCross, PriceAboveReference, PriceBelowReference


def make_ctx(**overrides):
    base = dict(
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        price=100.0,
        price_history=(),
        origin_price=None,
        entry_price=None,
        position_qty=0.0,
    )
    base.update(overrides)
    return StrategyContext(**base)


class TestComputeEntryPrice:
    def test_deviation_below_reference(self):
        assert compute_entry_price(1000.0, 0.75) == 992.5

    def test_zero_deviation_returns_reference(self):
        assert compute_entry_price(1000.0, 0.0) == 1000.0


class TestDeviationFromReferenceEntry:
    def test_rule_is_price_below_reference_with_matching_deviation(self):
        plugin = DeviationFromReferenceEntry(deviation_pct=0.75)
        assert isinstance(plugin.rule, PriceBelowReference)
        assert plugin.rule.deviation_pct == 0.75

    def test_entry_price_uses_origin_price_not_current_price(self):
        plugin = DeviationFromReferenceEntry(deviation_pct=0.75)
        ctx = make_ctx(price=1200.0, origin_price=1000.0)
        assert plugin.entry_price(ctx) == compute_entry_price(1000.0, 0.75)


class TestComputeShortEntryPrice:
    def test_deviation_above_reference(self):
        assert compute_short_entry_price(1000.0, 0.75) == 1007.5

    def test_zero_deviation_returns_reference(self):
        assert compute_short_entry_price(1000.0, 0.0) == 1000.0


class TestShortDeviationFromReferenceEntry:
    """DeviationFromReferenceEntry 的鏡像版本——見
    docs/ARCHITECTURE.md §6.11。"""

    def test_rule_is_price_above_reference_with_matching_deviation(self):
        plugin = ShortDeviationFromReferenceEntry(deviation_pct=0.75)
        assert isinstance(plugin.rule, PriceAboveReference)
        assert plugin.rule.deviation_pct == 0.75

    def test_entry_price_uses_origin_price_not_current_price(self):
        plugin = ShortDeviationFromReferenceEntry(deviation_pct=0.75)
        ctx = make_ctx(price=800.0, origin_price=1000.0)
        assert plugin.entry_price(ctx) == compute_short_entry_price(1000.0, 0.75)


class TestMACrossoverEntry:
    def test_rule_is_moving_average_cross_with_matching_windows(self):
        plugin = MACrossoverEntry(fast_window=5, slow_window=20)
        assert isinstance(plugin.rule, MovingAverageCross)
        assert (plugin.rule.fast_window, plugin.rule.slow_window) == (5, 20)

    def test_entry_price_is_current_price(self):
        plugin = MACrossoverEntry()
        assert plugin.entry_price(make_ctx(price=123.45)) == 123.45
