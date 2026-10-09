"""驗證 strategies/*.yaml 載入後,跟對應 demo 腳本手動組裝的版本結構
完全一樣——證明 DSL 只是「另一種寫法」,不是平行的第二套邏輯。"""

from pathlib import Path

from strategy_lab.dsl.loader import load_strategy
from strategy_lab.plugins.entry.resting_deviation_from_reference import RestingDeviationFromReferenceEntry
from strategy_lab.plugins.entry.ma_crossover import MACrossoverEntry
from strategy_lab.plugins.exit.bracket_tp_sl import BracketTPSLExit
from strategy_lab.plugins.exit.resting_return_to_reference import RestingReturnToReferenceExit
from strategy_lab.plugins.kill_switch.sustained_breakout import SustainedBreakoutKillSwitch
from strategy_lab.plugins.time_window.daily_session import DailySession
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "strategies"


class TestWeekendMeanReversionIsDeleted:
    def test_weekend_mean_reversion_yaml_no_longer_exists(self):
        """使用者要求刪除(2026-10-10);同一套進出場邏輯仍在 mean_reversion_breakout_guard.yaml(多一個 kill_switch)"""
        assert not (STRATEGIES_DIR / "weekend_mean_reversion.yaml").exists()


class TestMACrossoverBracketYaml:
    def test_matches_demo_crossover_phase1_hand_composed_version(self):
        strategy = load_strategy(STRATEGIES_DIR / "ma_crossover_bracket.yaml")
        assert strategy.entry == MACrossoverEntry(fast_window=5, slow_window=20)
        assert strategy.exit == BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5)
        assert strategy.time_window == DailySession(start_time="09:00", end_time="17:00", cleanup_buffer_minutes=2)


class TestMeanReversionBreakoutGuardYaml:
    def test_entry_exit_time_window(self):
        """一啟動就掛單的週末均值回歸(原本的 weekend_mean_reversion,已刪除)加上 kill_switch。"""
        strategy = load_strategy(STRATEGIES_DIR / "mean_reversion_breakout_guard.yaml")
        assert strategy.entry == RestingDeviationFromReferenceEntry(deviation_pct=0.75)
        assert strategy.exit == RestingReturnToReferenceExit()
        assert strategy.time_window == WeeklyWindow()

    def test_kill_switch_is_resolved_with_matching_params(self):
        strategy = load_strategy(STRATEGIES_DIR / "mean_reversion_breakout_guard.yaml")
        assert strategy.kill_switch == SustainedBreakoutKillSwitch(
            threshold_price=62000.0, reference_price=60000.0, hours=24.0, margin_pct=3.0
        )


class TestWeekendBandReversionYaml:
    def test_is_the_two_sided_band_strategy(self):
        """2026-10-10 改版:上下各掛一張(買、賣的 % 分開設),成交一張就在對面補一張,
        持倉在 ±1 之間切換直到收尾;direction both、loop null。"""
        from strategy_lab.plugins.entry.band import BandEntry
        from strategy_lab.plugins.exit.band import BandExit

        strategy = load_strategy(STRATEGIES_DIR / "weekend_band_reversion.yaml")
        assert strategy.band is True and strategy.scale_in is False
        assert isinstance(strategy.entry, BandEntry) and isinstance(strategy.exit, BandExit)
        assert strategy.entry.buy_pct > 0 and strategy.entry.sell_pct > 0
        assert strategy.direction == "both" and strategy.loop is None
        assert strategy.time_window == WeeklyWindow()


class TestEveryStrategyDeclaresLoop:
    """loop 預設 0(只做 1 個 event),既有策略都要明確寫出來,不能默默從
    「不限次數」變成「做一輪就停」。"""

    def test_each_yaml_has_an_explicit_loop_key(self):
        import yaml

        for path in sorted(STRATEGIES_DIR.glob("*.yaml")):
            assert "loop" in yaml.safe_load(path.read_text()), path.name

    def test_mean_reversion_breakout_guard_loops_without_limit(self):
        assert load_strategy(STRATEGIES_DIR / "mean_reversion_breakout_guard.yaml").loop is None


class TestScaleIn:
    def test_every_yaml_declares_scale_in_explicitly(self):
        import yaml

        for path in sorted(STRATEGIES_DIR.glob("*.yaml")):
            assert "scale_in" in yaml.safe_load(path.read_text()), path.name

    def test_scale_in_ladder_yaml(self):
        from strategy_lab.plugins.entry.scale_in import ScaleInEntry
        from strategy_lab.plugins.exit.scale_out import ScaleOutExit

        strategy = load_strategy(STRATEGIES_DIR / "scale_in_ladder.yaml")
        assert strategy.scale_in is True
        assert isinstance(strategy.entry, ScaleInEntry) and strategy.entry.weights == [2, 3, 1]
        assert isinstance(strategy.exit, ScaleOutExit)

    def test_scale_in_flag_must_match_plugins(self, tmp_path):
        import pytest

        src = (STRATEGIES_DIR / "scale_in_ladder.yaml").read_text()
        bad = tmp_path / "bad.yaml"
        bad.write_text(src.replace("scale_in: true", "scale_in: false"))
        with pytest.raises(ValueError, match="scale_in"):
            load_strategy(bad)


class TestExpectedHold:
    """策略的預估持倉時間 expected_hold(2026-10-09):超過就 🟡 警告,不自動平倉。"""

    def _write(self, tmp_path, extra):
        src = (STRATEGIES_DIR / "scale_in_ladder.yaml").read_text()
        path = tmp_path / "s.yaml"
        path.write_text(src + extra)
        return path

    def test_parsed_into_a_timedelta(self, tmp_path):
        from datetime import timedelta

        strategy = load_strategy(self._write(tmp_path, "\nexpected_hold:\n  value: 4\n  unit: hours\n"))
        assert strategy.expected_hold == timedelta(hours=4)

    def test_default_is_none(self):
        assert load_strategy(STRATEGIES_DIR / "scale_in_ladder.yaml").expected_hold is None

    def test_bad_unit_rejected(self, tmp_path):
        import pytest

        with pytest.raises(ValueError, match="expected_hold"):
            load_strategy(self._write(tmp_path, "\nexpected_hold:\n  value: 4\n  unit: weeks\n"))

    def test_only_for_scale_in_strategies(self, tmp_path):
        import pytest

        src = (STRATEGIES_DIR / "mean_reversion_breakout_guard.yaml").read_text()
        path = tmp_path / "w.yaml"
        path.write_text(src + "\nexpected_hold:\n  value: 4\n  unit: hours\n")
        with pytest.raises(ValueError, match="expected_hold"):
            load_strategy(path)
