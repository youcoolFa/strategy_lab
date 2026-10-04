"""分注 runner(PaperBroker,不碰網路):
- 每注建倉價由使用者輸入;數量照比重(2:3:1);一啟動就把各注建倉單掛上
- 每注成交後馬上掛該注的平倉單:建倉價 ± 距離,數量 = 該注數量
- 部位 0 → 0 = 1 個 loop(建 1 平 1、建 2 平 2、建 3 平 3 都是 1 個)
- loop 結束時取消還沒成交的建倉單,下一個 loop 三注重新掛上
"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.engine.runner import RunState
from strategy_lab.engine.scale_in_runner import ScaleInRunner
from strategy_lab.plugins.entry.scale_in import ScaleInEntry
from strategy_lab.plugins.exit.scale_out import ScaleOutExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

NOW = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
PRICES = [1000.0, 990.0, 980.0]


def make(direction="long", loop=None, entry_prices=None, records=None):
    runner = ScaleInRunner(
        entry=ScaleInEntry(weights=[2, 3, 1]),
        exit=ScaleOutExit(distance={"value": 1.0, "unit": "pct"}),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=2.0,  # 第一注數量
        entry_prices=entry_prices or PRICES,
        direction=direction,
        loop=loop,
        on_order=(records.append if records is not None else None),
    )
    runner.start(NOW, price=1005.0)
    return runner


def at(m):
    return NOW + timedelta(minutes=m)


def open_orders(records, purpose):
    state = {}
    for r in records:
        state[r.order_id] = r
    return sorted([r for r in state.values() if r.purpose == purpose and r.status == "open"], key=lambda r: r.lot)


class TestPlacement:
    def test_first_tick_places_all_lots_at_user_prices_with_weighted_qty(self):
        records = []
        runner = make(records=records)
        runner.tick(at(0), 1005.0)

        entries = open_orders(records, "entry")
        assert [(r.lot, r.side, r.price, r.qty) for r in entries] == [
            (1, "Buy", 1000.0, 2.0), (2, "Buy", 990.0, 3.0), (3, "Buy", 980.0, 1.0),
        ]
        assert runner.state == RunState.ENTRY_PENDING

    def test_lot_fill_places_its_own_exit_at_entry_plus_distance(self):
        records = []
        runner = make(records=records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)  # 第一注成交

        exits = open_orders(records, "exit")
        assert [(r.lot, r.side, r.price, r.qty, r.reduce_only) for r in exits] == [(1, "Sell", pytest.approx(1010.0), 2.0, True)]
        assert runner.state == RunState.IN_POSITION

    def test_entry_prices_must_match_number_of_lots(self):
        with pytest.raises(ValueError, match="entry_prices"):
            make(entry_prices=[1000.0, 990.0])

    def test_market_order_type_is_rejected(self):
        with pytest.raises(ValueError, match="limit"):
            ScaleInRunner(
                entry=ScaleInEntry(weights=[2, 3, 1]), exit=ScaleOutExit(distance={"value": 1, "unit": "pct"}),
                time_window=WeeklyWindow(), order_qty=1.0, entry_prices=PRICES, order_type="market",
            )


class TestLoopDefinition:
    def test_build_one_close_one_is_one_loop_and_unfilled_entries_are_cancelled(self):
        records = []
        runner = make(records=records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)  # 第一注成交 @1000
        runner.tick(at(2), 1011.0)  # 第一注平倉 @1010 → 部位 0 → loop 結束

        assert len(runner.events) == 1
        assert runner.events[0].realized_pnl == pytest.approx((1010 - 1000) * 2.0)
        assert open_orders(records, "entry") == []  # 第二、三注建倉單已取消
        cancelled = {r.lot for r in records if r.purpose == "entry" and r.status == "canceled"}
        assert cancelled == {2, 3}

        runner.tick(at(3), 1011.0)  # 新 loop:三注重新掛上
        assert [r.lot for r in open_orders(records, "entry")] == [1, 2, 3]
        assert all(r.event_index == 2 for r in open_orders(records, "entry"))

    def test_build_two_close_two_is_one_loop(self):
        runner = make()
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 989.0)  # 第一、二注成交(1000、990)
        runner.tick(at(2), 1000.0)  # 第二注平倉 @999.9 成交;部位還有第一注 → 不算 loop
        assert runner.events == []
        runner.tick(at(3), 1011.0)  # 第一注平倉 @1010 → 部位 0

        assert len(runner.events) == 1
        event = runner.events[0]
        assert event.fills == 4
        expected = (1010 - 1000) * 2.0 + (990 * 1.01 - 990) * 3.0
        assert event.realized_pnl == pytest.approx(expected)

    def test_build_three_close_three_is_one_loop(self):
        runner = make()
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 979.0)  # 三注都成交
        runner.tick(at(2), 1011.0)  # 三注的平倉價(1010、999.9、989.8)都碰到
        assert len(runner.events) == 1
        assert runner.events[0].fills == 6
        assert len(runner.trades) == 3

    def test_loop_zero_stops_after_first_loop(self):
        runner = make(loop=0)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)
        runner.tick(at(2), 1011.0)
        assert runner.state == RunState.STOPPED
        assert runner.stop_reason == "loop_done"


class TestShortAndCleanup:
    def test_short_sells_at_user_prices_and_buys_back_below(self):
        records = []
        runner = make(direction="short", entry_prices=[1000.0, 1010.0, 1020.0], records=records)
        runner.tick(at(0), 995.0)
        assert [r.side for r in open_orders(records, "entry")] == ["Sell", "Sell", "Sell"]
        runner.tick(at(1), 1001.0)  # 第一注賣出成交
        [exit_order] = open_orders(records, "exit")
        assert (exit_order.side, exit_order.price) == ("Buy", pytest.approx(990.0))
        runner.tick(at(2), 989.0)
        assert runner.events[0].realized_pnl == pytest.approx((1000 - 990) * 2.0)

    def test_window_cleanup_cancels_everything_and_market_closes(self):
        records = []
        runner = make(records=records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 989.0)  # 第一、二注成交

        runner.tick(runner.window_end - timedelta(minutes=1), 985.0)

        assert runner.state == RunState.STOPPED and runner.stop_reason == "window_cleanup"
        assert open_orders(records, "entry") == [] and open_orders(records, "exit") == []
        assert runner.broker.position_qty() == 0.0
        assert runner.events[-1].forced is True
