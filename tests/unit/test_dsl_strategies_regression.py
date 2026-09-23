"""驗證 strategies/*.yaml 載入後,跟對應 demo 腳本手動組裝的版本結構
完全一樣——證明 DSL 只是「另一種寫法」,不是平行的第二套邏輯。"""

from pathlib import Path

from strategy_lab.dsl.loader import load_strategy
from strategy_lab.plugins.entry.deviation_from_reference import DeviationFromReferenceEntry
from strategy_lab.plugins.entry.ma_crossover import MACrossoverEntry
from strategy_lab.plugins.exit.bracket_tp_sl import BracketTPSLExit
from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit
from strategy_lab.plugins.time_window.daily_session import DailySession
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "strategies"


class TestWeekendMeanReversionYaml:
    def test_matches_demo_weekend_phase1_hand_composed_version(self):
        strategy = load_strategy(STRATEGIES_DIR / "weekend_mean_reversion.yaml")
        assert strategy.order_qty == 1.0
        assert strategy.entry == DeviationFromReferenceEntry(deviation_pct=0.75)
        assert strategy.exit == ReturnToReferenceExit()
        assert strategy.time_window == WeeklyWindow()  # 跟 demo 一樣全用預設值


class TestMACrossoverBracketYaml:
    def test_matches_demo_crossover_phase1_hand_composed_version(self):
        strategy = load_strategy(STRATEGIES_DIR / "ma_crossover_bracket.yaml")
        assert strategy.order_qty == 1.0
        assert strategy.entry == MACrossoverEntry(fast_window=5, slow_window=20)
        assert strategy.exit == BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5)
        assert strategy.time_window == DailySession(start_time="09:00", end_time="17:00", cleanup_buffer_minutes=2)
