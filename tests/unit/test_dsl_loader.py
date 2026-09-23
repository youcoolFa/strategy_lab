"""dsl/loader.py:YAML -> 驗證過的 schema -> 透過 registry.get() 解析成
真正的 plugin 物件。這是 registry.get() 第一次真正被程式碼呼叫的地方
(見 docs/ARCHITECTURE.md §4.3「Registry 死碼路徑」)。"""

import pytest
import yaml
from pydantic import ValidationError

from strategy_lab.dsl.loader import load_strategy
from strategy_lab.plugins.entry.deviation_from_reference import DeviationFromReferenceEntry
from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit
from strategy_lab.plugins.kill_switch.sustained_breakout import SustainedBreakoutKillSwitch
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow
from strategy_lab.registry import UnknownPlugin

WEEKEND_YAML = """
name: weekend_mean_reversion
symbol: BTC/USDT
order_qty: 1.0
entry:
  type: deviation_from_reference
  params:
    deviation_pct: 0.75
exit:
  type: return_to_reference
  params: {}
time_window:
  type: weekly_window
  params:
    end_weekday: 0
    end_time: "06:00"
"""


def write_yaml(tmp_path, content: str, filename: str = "strategy.yaml"):
    path = tmp_path / filename
    path.write_text(content)
    return path


class TestLoadStrategyBasics:
    def test_resolves_entry_exit_time_window_to_real_plugin_instances(self, tmp_path):
        path = write_yaml(tmp_path, WEEKEND_YAML)
        strategy = load_strategy(path)

        assert strategy.name == "weekend_mean_reversion"
        assert strategy.symbol == "BTC/USDT"
        assert strategy.order_qty == 1.0
        assert isinstance(strategy.entry, DeviationFromReferenceEntry)
        assert isinstance(strategy.exit, ReturnToReferenceExit)
        assert isinstance(strategy.time_window, WeeklyWindow)
        assert strategy.kill_switch is None

    def test_loaded_entry_is_structurally_equal_to_hand_composed(self, tmp_path):
        path = write_yaml(tmp_path, WEEKEND_YAML)
        strategy = load_strategy(path)
        assert strategy.entry == DeviationFromReferenceEntry(deviation_pct=0.75)
        assert strategy.exit == ReturnToReferenceExit()

    def test_kill_switch_resolved_when_present(self, tmp_path):
        yaml_with_kill_switch = WEEKEND_YAML + """
kill_switch:
  type: sustained_breakout
  params:
    threshold_price: 1010.0
    reference_price: 1000.0
    margin_pct: 3.0
"""
        path = write_yaml(tmp_path, yaml_with_kill_switch)
        strategy = load_strategy(path)
        assert isinstance(strategy.kill_switch, SustainedBreakoutKillSwitch)
        assert strategy.kill_switch == SustainedBreakoutKillSwitch(
            threshold_price=1010.0, reference_price=1000.0, margin_pct=3.0
        )


class TestLoadStrategyErrors:
    def test_unregistered_plugin_type_raises_unknown_plugin(self, tmp_path):
        bad_yaml = WEEKEND_YAML.replace("deviation_from_reference", "does_not_exist")
        path = write_yaml(tmp_path, bad_yaml)
        with pytest.raises(UnknownPlugin):
            load_strategy(path)

    def test_invalid_yaml_shape_raises_validation_error(self, tmp_path):
        incomplete = yaml.safe_load(WEEKEND_YAML)
        del incomplete["entry"]
        path = write_yaml(tmp_path, yaml.dump(incomplete))
        with pytest.raises(ValidationError):
            load_strategy(path)
