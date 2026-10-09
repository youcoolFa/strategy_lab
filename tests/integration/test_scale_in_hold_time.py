"""分注 runner 的持倉計時(2026-10-09):
- 每注建倉成交 → 平倉成交的時間,記在平倉單的 OrderRecord.hold_seconds,也收進 runner.lot_holds
- 策略的 expected_hold:持倉超過就發一次 WARNING(🟡 Telegram),不自動平倉
- 收尾強制平倉時還拿著的注也計時(forced)
"""

from datetime import datetime, timedelta, timezone

import pytest
from loguru import logger

from strategy_lab.engine.scale_in_runner import ScaleInRunner
from strategy_lab.plugins.entry.scale_in import ScaleInEntry
from strategy_lab.plugins.exit.scale_out import ScaleOutExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

NOW = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)  # 週六
PRICES = [1000.0, 990.0, 980.0]


def make(records, expected_hold=None):
    runner = ScaleInRunner(
        entry=ScaleInEntry(weights=[2, 3, 1]),
        exit=ScaleOutExit(distance={"value": 1.0, "unit": "pct"}),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=2.0, entry_prices=PRICES, on_order=records.append, expected_hold=expected_hold,
    )
    runner.start(NOW, price=1005.0)
    return runner


def at(m):
    return NOW + timedelta(minutes=m)


@pytest.fixture
def warnings():
    got = []
    sink = logger.add(lambda m: got.append(m.record["message"]), level="WARNING")
    yield got
    logger.remove(sink)


def closed_exits(records):
    return {r.lot: r for r in records if r.purpose == "exit" and r.status == "closed"}


class TestHoldTime:
    def test_exit_fill_records_how_long_the_lot_was_held(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(10), 999.0)  # 第1注建倉成交
        runner.tick(at(200), 1011.0)  # 第1注平倉成交(持倉 190 分)

        assert closed_exits(records)[1].hold_seconds == 190 * 60
        [hold] = runner.lot_holds
        assert (hold.index, hold.event_index, hold.seconds, hold.forced) == (1, 1, 190 * 60, False)

    def test_entry_and_open_records_have_no_hold_time(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(10), 999.0)
        assert all(r.hold_seconds is None for r in records)

    def test_each_lot_is_timed_from_its_own_fill(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(10), 999.0)  # 第1注 @ 10
        runner.tick(at(30), 989.0)  # 第2注 @ 30
        runner.tick(at(60), 1001.0)  # 第2注平倉(990 × 1.01 = 999.9)@ 60 → 30 分
        runner.tick(at(100), 1011.0)  # 第1注平倉 @ 100 → 90 分

        exits = closed_exits(records)
        assert exits[2].hold_seconds == 30 * 60
        assert exits[1].hold_seconds == 90 * 60


class TestExpectedHoldWarning:
    def test_warns_once_per_lot_when_held_longer_than_expected(self, warnings):
        records = []
        runner = make(records, expected_hold=timedelta(hours=2))
        runner.tick(at(0), 1005.0)
        runner.tick(at(10), 999.0)  # 第1注建倉
        runner.tick(at(100), 999.0)  # 1.5 小時:還沒超過
        assert not any("預估" in w for w in warnings)
        runner.tick(at(131), 999.0)  # 2 小時 1 分:超過
        runner.tick(at(200), 999.0)  # 不重複警告
        over = [w for w in warnings if "預估" in w]
        assert len(over) == 1
        assert "第1注" in over[0] and "2 小時 1 分" in over[0] and "不會自動平倉" in over[0]
        assert runner.lots[0].holding  # 沒有被平掉

    def test_no_estimate_no_warning(self, warnings):
        records = []
        runner = make(records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(10), 999.0)
        runner.tick(at(60 * 24), 999.0)
        assert not any("預估" in w for w in warnings)

    def test_exceeded_lot_is_marked_in_its_hold(self):
        records = []
        runner = make(records, expected_hold=timedelta(hours=1))
        runner.tick(at(0), 1005.0)
        runner.tick(at(10), 999.0)
        runner.tick(at(200), 1011.0)
        [hold] = runner.lot_holds
        assert hold.seconds > 3600


class TestForcedCloseAtCleanup:
    def test_lots_still_held_at_cleanup_are_timed_as_forced(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(10), 999.0)  # 第1注
        runner.tick(at(20), 989.0)  # 第2注
        runner.request_stop()
        runner.tick(at(80), 985.0)  # 收尾:市價平掉兩注

        holds = sorted((h.index, h.seconds, h.forced) for h in runner.lot_holds)
        assert holds == [(1, 70 * 60, True), (2, 60 * 60, True)]
