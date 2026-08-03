"""One full-cycle integration test per demo strategy: entry placed -> filled
-> exit placed -> filled -> back to idle -> cleanup at window end. Uses
hand-picked deterministic price sequences (not SyntheticFeed) so the test
isn't tied to any RNG behavior."""

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
        runner.start(now, price=1000.0)  # origin_price=1000, entry target=990

        runner.tick(now, 1000.0)
        assert runner.state == RunState.ENTRY_PENDING

        runner.tick(now + timedelta(minutes=5), 985.0)  # crosses entry price -> filled
        assert runner.state == RunState.IN_POSITION
        assert runner.active_entry_price == 990.0

        runner.tick(now + timedelta(minutes=10), 985.0)  # exit order placed at origin=1000
        assert runner.state == RunState.EXIT_PENDING

        runner.tick(now + timedelta(minutes=15), 1000.0)  # price returns to origin -> exit filled
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

        prices = [1000.0, 1000.0, 1000.0, 1000.0, 1020.0]  # last sample triggers the crossover
        runner.start(now, prices[0])
        for i, price in enumerate(prices):
            runner.tick(now + timedelta(minutes=i), price)
        assert runner.state == RunState.ENTRY_PENDING

        runner.tick(now + timedelta(minutes=5), 1020.0)  # marketable limit fills same price
        assert runner.state == RunState.IN_POSITION
        assert runner.active_entry_price == 1020.0

        runner.tick(now + timedelta(minutes=6), 1031.0)  # +1.08% -> take-profit triggers
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
