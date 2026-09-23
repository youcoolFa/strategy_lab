"""kill_switch plugin 只負責:在 __post_init__ 組出正確的 Condition。
真正的觸發邏輯真值表測試在 tests/unit/test_rules_conditions.py。"""

from datetime import timedelta

from strategy_lab.plugins.kill_switch.sustained_breakout import SustainedBreakoutKillSwitch
from strategy_lab.rules.conditions import SustainedPriceBreakout


class TestSustainedBreakoutKillSwitch:
    def test_rule_is_sustained_price_breakout_with_matching_params(self):
        plugin = SustainedBreakoutKillSwitch(
            threshold_price=100.0, reference_price=90.0, hours=72.0, margin_pct=5.0
        )
        assert isinstance(plugin.rule, SustainedPriceBreakout)
        assert plugin.rule.threshold_price == 100.0
        assert plugin.rule.reference_price == 90.0
        assert plugin.rule.hours == 72.0
        assert plugin.rule.margin_pct == 5.0

    def test_default_hours_is_seventy_two(self):
        plugin = SustainedBreakoutKillSwitch(threshold_price=100.0, reference_price=90.0, margin_fixed=10.0)
        assert plugin.rule.hours == 72.0

    def test_days_param_is_forwarded_and_normalized_onto_the_wrapper_too(self):
        plugin = SustainedBreakoutKillSwitch(
            threshold_price=100.0, reference_price=90.0, days=1.0, margin_pct=5.0
        )
        assert plugin.hours == 24.0  # wrapper 自己的 hours 也正規化,不用挖 .rule 才看得到
        assert plugin.rule.hours == 24.0

    def test_window_duration_matches_resolved_hours(self):
        plugin = SustainedBreakoutKillSwitch(
            threshold_price=100.0, reference_price=90.0, minutes=90.0, margin_pct=5.0
        )
        assert plugin.window_duration == timedelta(minutes=90)
