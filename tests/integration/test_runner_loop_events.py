"""loop = 重複次數:總 event 數 = loop + 1,做完就收尾結束;loop=None 不限
次數(做到時間窗結束)。每個 event(部位 0 → 0)都記錄損益和期間最大回撤,
包括時間窗收尾被強制平倉的那一筆。"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.engine.runner import RunState, StrategyRunner
from strategy_lab.plugins.entry.resting_deviation_from_reference import RestingDeviationFromReferenceEntry
from strategy_lab.plugins.exit.resting_return_to_reference import RestingReturnToReferenceExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

NOW = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)


def make_runner(loop, on_event=None):
    return StrategyRunner(
        entry=RestingDeviationFromReferenceEntry(deviation_pct=1.0),
        exit=RestingReturnToReferenceExit(),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=1.0,
        loop=loop,
        on_event=on_event,
    )


def one_round_trip(runner, start_minute):
    """買 990 成交 → 掛平倉 1000 → 成交,共 4 個 tick。"""
    m = start_minute
    runner.tick(NOW + timedelta(minutes=m), 1000.0)  # IDLE → 掛買 990
    runner.tick(NOW + timedelta(minutes=m + 1), 985.0)  # 成交
    runner.tick(NOW + timedelta(minutes=m + 2), 985.0)  # 掛平倉 1000
    runner.tick(NOW + timedelta(minutes=m + 3), 1000.0)  # 成交 → event 完成


class TestLoopCount:
    def test_loop_zero_does_one_event_then_stops_without_new_entry(self):
        runner = make_runner(loop=0)
        runner.start(NOW, price=1000.0)

        one_round_trip(runner, 0)

        assert len(runner.events) == 1
        assert runner.state == RunState.STOPPED
        runner.tick(NOW + timedelta(minutes=10), 1000.0)
        assert runner.state == RunState.STOPPED  # 不會再掛第 2 張進場單

    def test_loop_two_does_three_events(self):
        runner = make_runner(loop=2)
        runner.start(NOW, price=1000.0)

        for i in range(3):
            assert runner.state != RunState.STOPPED
            one_round_trip(runner, i * 10)

        assert len(runner.events) == 3
        assert runner.state == RunState.STOPPED

    def test_loop_none_keeps_going(self):
        runner = make_runner(loop=None)
        runner.start(NOW, price=1000.0)

        for i in range(5):
            one_round_trip(runner, i * 10)

        assert len(runner.events) == 5
        assert runner.state != RunState.STOPPED

    def test_negative_loop_is_rejected(self):
        with pytest.raises(ValueError, match="loop"):
            make_runner(loop=-1)


class TestEventRecording:
    def test_event_has_pnl_and_drawdown_measured_from_ticks(self):
        runner = make_runner(loop=None)
        runner.start(NOW, price=1000.0)
        runner.tick(NOW, 1000.0)  # 掛買 990
        runner.tick(NOW + timedelta(minutes=1), 985.0)  # 成交 @990
        runner.tick(NOW + timedelta(minutes=2), 975.0)  # 掛平倉;帳面 -15
        runner.tick(NOW + timedelta(minutes=3), 1000.0)  # 平倉成交

        event = runner.events[0]
        assert event.realized_pnl == pytest.approx(10.0)
        assert event.max_drawdown == pytest.approx(-15.0)
        assert event.forced is False

    def test_window_cleanup_records_forced_event(self):
        runner = make_runner(loop=None)
        runner.start(NOW, price=1000.0)
        runner.tick(NOW, 1000.0)
        runner.tick(NOW + timedelta(minutes=1), 985.0)  # 買進成交 @990
        runner.tick(NOW + timedelta(minutes=2), 985.0)  # 掛平倉

        runner.tick(runner.window_end - timedelta(minutes=1), 970.0)  # 收尾 → 市價平倉

        assert runner.state == RunState.STOPPED
        event = runner.events[-1]
        assert event.forced is True
        assert event.realized_pnl == pytest.approx(970.0 - 990.0)
        assert runner.trades == []  # 強制平倉仍不算進 trades(既有設計),但有記成 event

    def test_on_event_callback_is_called_for_each_completed_event(self):
        seen = []
        runner = make_runner(loop=1, on_event=seen.append)
        runner.start(NOW, price=1000.0)

        one_round_trip(runner, 0)
        one_round_trip(runner, 10)

        assert [e.index for e in seen] == [1, 2]
