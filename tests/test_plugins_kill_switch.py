"""kill_switch plugin 只負責:在 __post_init__ 組出正確的 Condition。
真正的觸發邏輯真值表測試在 tests/test_rules_conditions.py。"""

from strategy_lab.plugins.kill_switch.sustained_breakout import SustainedBreakoutKillSwitch
from strategy_lab.rules.conditions import SustainedPriceBreakout


class TestSustainedBreakoutKillSwitch:
    def test_rule_is_sustained_price_breakout_with_matching_params(self):
        plugin = SustainedBreakoutKillSwitch(
            threshold_price=100.0, reference_price=90.0, days=3, margin_pct=5.0
        )
        assert isinstance(plugin.rule, SustainedPriceBreakout)
        assert plugin.rule.threshold_price == 100.0
        assert plugin.rule.reference_price == 90.0
        assert plugin.rule.days == 3
        assert plugin.rule.margin_pct == 5.0

    def test_default_days_is_three(self):
        plugin = SustainedBreakoutKillSwitch(threshold_price=100.0, reference_price=90.0, margin_fixed=10.0)
        assert plugin.rule.days == 3
