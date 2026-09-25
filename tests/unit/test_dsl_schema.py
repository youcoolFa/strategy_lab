"""dsl/schema.py 的 pydantic schema 驗證測試——只測「YAML 讀進來的 dict
合不合法」,不涉及把 type 字串解析成實際的 plugin class(那是
loader.py 的職責,見 test_dsl_loader.py)。"""

import pytest
from pydantic import ValidationError

from strategy_lab.dsl.schema import PluginSpec, StrategyDefinition

VALID_DEFINITION = {
    "name": "weekend_mean_reversion",
    "symbol": "BTC/USDT",
    "entry": {"type": "deviation_from_reference", "params": {"deviation_pct": 0.75}},
    "exit": {"type": "return_to_reference", "params": {}},
    "time_window": {"type": "weekly_window", "params": {"end_weekday": 0, "end_time": "06:00"}},
}


class TestPluginSpec:
    def test_params_defaults_to_empty_dict_when_omitted(self):
        spec = PluginSpec(type="return_to_reference")
        assert spec.params == {}

    def test_unknown_field_raises(self):
        with pytest.raises(ValidationError):
            PluginSpec(type="return_to_reference", params={}, typo_field="oops")


class TestStrategyDefinition:
    def test_valid_full_definition_parses(self):
        definition = StrategyDefinition(**VALID_DEFINITION)
        assert definition.name == "weekend_mean_reversion"
        assert definition.entry.type == "deviation_from_reference"
        assert definition.entry.params == {"deviation_pct": 0.75}
        assert definition.kill_switch is None

    def test_kill_switch_is_optional_and_resolves_when_present(self):
        data = {
            **VALID_DEFINITION,
            "kill_switch": {
                "type": "sustained_breakout",
                "params": {"threshold_price": 1010.0, "reference_price": 1000.0, "margin_pct": 3.0},
            },
        }
        definition = StrategyDefinition(**data)
        assert definition.kill_switch is not None
        assert definition.kill_switch.type == "sustained_breakout"

    def test_missing_required_field_raises(self):
        incomplete = {k: v for k, v in VALID_DEFINITION.items() if k != "entry"}
        with pytest.raises(ValidationError):
            StrategyDefinition(**incomplete)

    def test_unknown_top_level_field_raises(self):
        with pytest.raises(ValidationError):
            StrategyDefinition(**VALID_DEFINITION, typo_field="oops")
