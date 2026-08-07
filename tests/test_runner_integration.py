"""每個示範策略各一個完整流程的整合測試:下單進場 → 成交 → 下單出場 →
成交 → 回到 idle → 窗口結束時清理。用手動挑選的確定性價格序列(不是
SyntheticFeed),所以測試不會被任何隨機數行為綁住。"""

from datetime import datetime, timedelta, timezone

from strategy_lab.engine.runner import RunState, StrategyRunner
from strategy_lab.plugins.entry.deviation_from_reference import DeviationFromReferenceEntry
from strategy_lab.plugins.entry.ma_crossover import MACrossoverEntry
from strategy_lab.plugins.exit.bracket_tp_sl import BracketTPSLExit
from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit
from strategy_lab.plugins.time_window.daily_session import DailySession
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow


class TestWeekendStrategyFullCycle:
    def test_entry_fill_exit_fill_then_cleanup(self):
        runner = StrategyRunner(
            entry=DeviationFromReferenceEntry(deviation_pct=1.0),
            exit=ReturnToReferenceExit(),
            time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
            order_qty=1.0,
        )
        now = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
        runner.start(now, price=1000.0)  # origin_price=1000,進場目標=990

        # Phase 2 起,PriceBelowReference 是真的每 tick 檢查 ctx.price,
        # 不再是「永遠 True、觸發交給掛單價」——所以價格還沒跌破目標時
        # 不會下單,跟 Phase 1 的行為不同。
        runner.tick(now, 1000.0)  # 價格==origin,還沒跌破 990
        assert runner.state == RunState.IDLE

        runner.tick(now + timedelta(minutes=5), 995.0)  # 仍未跌破 990
        assert runner.state == RunState.IDLE

        runner.tick(now + timedelta(minutes=10), 989.0)  # 跌破 990,條件成立 -> 下單
        assert runner.state == RunState.ENTRY_PENDING

        runner.tick(now + timedelta(minutes=15), 989.0)  # 限價單成交
        assert runner.state == RunState.IN_POSITION
        assert runner.active_entry_price == 990.0

        runner.tick(now + timedelta(minutes=20), 995.0)  # 還沒回到 origin=1000
        assert runner.state == RunState.IN_POSITION

        runner.tick(now + timedelta(minutes=25), 1000.0)  # 回到 origin,條件成立 -> 下單
        assert runner.state == RunState.EXIT_PENDING

        runner.tick(now + timedelta(minutes=30), 1000.0)  # 出場單成交
        assert runner.state == RunState.IDLE
        assert len(runner.trades) == 1
        assert runner.trades[0].entry_price == 990.0
        assert runner.trades[0].exit_price == 1000.0

        cleanup_time = runner.window_end - timedelta(minutes=1)
        runner.tick(cleanup_time, 1000.0)
        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0.0


class TestCrossoverStrategyFullCycle:
    def test_entry_fill_exit_fill_then_cleanup(self):
        runner = StrategyRunner(
            entry=MACrossoverEntry(fast_window=2, slow_window=4),
            exit=BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5),
            time_window=DailySession(start_time="09:00", end_time="17:00", cleanup_buffer_minutes=2),
            order_qty=1.0,
        )
        now = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)

        prices = [1000.0, 1000.0, 1000.0, 1000.0, 1020.0]  # 最後一筆觸發交叉
        runner.start(now, prices[0])
        for i, price in enumerate(prices):
            runner.tick(now + timedelta(minutes=i), price)
        assert runner.state == RunState.ENTRY_PENDING

        runner.tick(now + timedelta(minutes=5), 1020.0)  # 貼價限價單同價成交
        assert runner.state == RunState.IN_POSITION
        assert runner.active_entry_price == 1020.0

        runner.tick(now + timedelta(minutes=6), 1031.0)  # +1.08% -> 觸發停利
        assert runner.state == RunState.EXIT_PENDING

        runner.tick(now + timedelta(minutes=7), 1031.0)
        assert runner.state == RunState.IDLE
        assert len(runner.trades) == 1
        assert runner.trades[0].entry_price == 1020.0
        assert runner.trades[0].exit_price == 1031.0

        cleanup_time = runner.window_end - timedelta(minutes=1)
        runner.tick(cleanup_time, 1031.0)
        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0.0
