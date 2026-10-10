"""runner 每張單的下單/成交/取消都透過 on_order 回報(含用途與所屬 event),
停止時記錄原因——給 sl_order / sl_run 用。PaperBroker,不碰網路。"""

from datetime import datetime, timedelta, timezone

from strategy_lab.engine.runner import StrategyRunner
from strategy_lab.plugins.entry.resting_deviation_from_reference import RestingDeviationFromReferenceEntry
from strategy_lab.plugins.exit.resting_return_to_reference import RestingReturnToReferenceExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

NOW = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)


def make_runner(loop=None, direction="long"):
    records = []
    runner = StrategyRunner(
        entry=RestingDeviationFromReferenceEntry(deviation_pct=1.0),
        exit=RestingReturnToReferenceExit(),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=1.0, loop=loop, direction=direction, on_order=records.append,
    )
    runner.start(NOW, price=1000.0)
    return runner, records


def at(m):
    return NOW + timedelta(minutes=m)


class TestOrderRecords:
    def test_full_cycle_reports_placement_and_fill_with_purpose_and_event(self):
        runner, records = make_runner()
        runner.tick(at(0), 1000.0)  # 掛買
        runner.tick(at(1), 985.0)  # 成交
        runner.tick(at(2), 985.0)  # 掛平倉
        runner.tick(at(3), 1000.0)  # 平倉成交

        summary = [(r.purpose, r.side, r.status, r.event_index) for r in records]
        assert summary == [
            ("entry", "Buy", "open", 1),
            ("entry", "Buy", "closed", 1),
            ("exit", "Sell", "open", 1),
            ("exit", "Sell", "closed", 1),
        ]
        assert records[1].avg_price == 990.0 and records[1].filled_qty == 1.0
        assert records[2].reduce_only is True

    def test_second_cycle_belongs_to_event_two(self):
        runner, records = make_runner()
        for i in range(2):
            runner.tick(at(i * 10), 1000.0)
            runner.tick(at(i * 10 + 1), 985.0)
            runner.tick(at(i * 10 + 2), 985.0)
            runner.tick(at(i * 10 + 3), 1000.0)
        assert [r.event_index for r in records if r.status == "open"] == [1, 1, 2, 2]

    def test_short_sides(self):
        runner, records = make_runner(direction="short")
        runner.tick(at(0), 1000.0)
        assert (records[0].purpose, records[0].side) == ("entry", "Sell")

    def test_cleanup_reports_cancel_and_forced_close(self):
        runner, records = make_runner()
        runner.tick(at(0), 1000.0)
        runner.tick(at(1), 985.0)  # 買進成交
        runner.tick(at(2), 985.0)  # 掛平倉(未成交)

        runner.tick(runner.window_end - timedelta(minutes=1), 970.0)

        tail = [(r.purpose, r.side, r.order_type, r.status) for r in records[3:]]
        assert ("exit", "Sell", "limit", "canceled") in tail
        assert ("forced_close", "Sell", "market", "open") in tail
        assert ("forced_close", "Sell", "market", "closed") in tail


class TestStopReason:
    def test_window_cleanup(self):
        runner, _ = make_runner()
        runner.tick(runner.window_end - timedelta(minutes=1), 1000.0)
        assert runner.stop_reason == "window_cleanup"

    def test_stop_requested(self):
        runner, _ = make_runner()
        runner.request_stop()
        runner.tick(at(0), 1000.0)
        assert runner.stop_reason == "stop_requested"

    def test_loop_done(self):
        runner, _ = make_runner(loop=0)
        runner.tick(at(0), 1000.0)
        runner.tick(at(1), 985.0)
        runner.tick(at(2), 985.0)
        runner.tick(at(3), 1000.0)
        assert runner.stop_reason == "loop_done"

    def test_still_running_has_no_reason(self):
        runner, _ = make_runner()
        runner.tick(at(0), 1000.0)
        assert runner.stop_reason is None


class TestHoldSeconds:
    """減倉的成交單記持倉秒數(這輪開始有倉 → 這張成交,2026-10-11);建倉單、掛單、取消都是 None。"""

    def test_exit_fill_has_hold_seconds_entry_does_not(self):
        runner, records = make_runner()
        runner.tick(at(0), 1000.0)
        runner.tick(at(1), 985.0)  # 建倉成交
        runner.tick(at(2), 985.0)
        runner.tick(at(31), 1000.0)  # 平倉成交

        holds = [(r.purpose, r.status, r.hold_seconds) for r in records]
        assert holds == [
            ("entry", "open", None), ("entry", "closed", None),
            ("exit", "open", None), ("exit", "closed", 30 * 60),
        ]

    def test_short_exit_also_has_hold_seconds(self):
        runner, records = make_runner(direction="short")
        runner.tick(at(0), 1000.0)
        runner.tick(at(1), 1015.0)  # 賣出建倉成交
        runner.tick(at(2), 1015.0)
        runner.tick(at(11), 1000.0)  # 買回平倉成交
        assert records[-1].status == "closed" and records[-1].hold_seconds == 10 * 60

    def test_forced_close_has_hold_seconds(self):
        runner, records = make_runner()
        runner.tick(at(0), 1000.0)
        runner.tick(at(1), 985.0)  # 買進成交
        cleanup_at = runner.window_end - timedelta(minutes=1)
        runner.tick(cleanup_at, 970.0)

        [forced] = [r for r in records if r.purpose == "forced_close" and r.status == "closed"]
        assert forced.hold_seconds == (cleanup_at - at(1)).total_seconds()
