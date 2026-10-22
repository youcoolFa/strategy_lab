"""區間 runner(engine/band_runner.py,sat_strategy;PaperBroker,不碰網路)。

2026-10-17 使用者要求改成「各自回到起點平倉」的邏輯(取代 10-10 的「成交後在對面價位補單、多空翻轉」):
- 啟動時上下各掛一張進場限價單:買 = origin × (1 − buy_pct%)、賣 = origin × (1 + sell_pct%)
- 哪一邊成交,就立刻在 origin 掛反向的平倉限價單(reduce_only),另一邊的進場單繼續掛著
- 平倉單成交 → 把這一邊的進場單補回去,回到最初狀態(兩邊進場單都掛著);可以一直循環,沒有次數上限
- 時間窗結束:取消所有掛單、市價平倉
"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.engine.band_runner import BandRunner
from strategy_lab.engine.runner import RunState
from strategy_lab.plugins.entry.band import BandEntry
from strategy_lab.plugins.exit.band import BandExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

NOW = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)  # 週六
QTY = 2.0


def make(records=None, origin=100.0):
    runner = BandRunner(
        entry=BandEntry(buy_pct=1.0, sell_pct=1.0), exit=BandExit(),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=QTY, direction="both", loop=None, on_order=(records.append if records is not None else None),
    )
    runner.start(NOW, price=origin)
    return runner


def at(m):
    return NOW + timedelta(minutes=m)


def _latest(records):
    state = {}
    for r in records:
        state[r.order_id] = r
    return state.values()


def resting(records):
    """交易所上還掛著的單:(方向, 價位, 數量),排序過。"""
    return sorted((r.side, r.price, r.qty) for r in _latest(records) if r.status == "open")


def resting_detail(records):
    return sorted((r.side, r.price, r.purpose, r.reduce_only) for r in _latest(records) if r.status == "open")


INITIAL = [("Buy", pytest.approx(99.0), QTY), ("Sell", pytest.approx(101.0), QTY)]


class TestStart:
    def test_first_tick_places_one_buy_below_and_one_sell_above(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        assert resting(records) == INITIAL
        assert all(r.purpose == "entry" and r.reduce_only is False for r in records)
        assert runner.state == RunState.ENTRY_PENDING

    def test_not_a_band_entry_is_rejected(self):
        from strategy_lab.plugins.entry.scale_in import ScaleInEntry

        with pytest.raises(ValueError, match="band"):
            BandRunner(entry=ScaleInEntry(weights=[1]), exit=BandExit(), time_window=WeeklyWindow(), order_qty=1.0)


class TestEntryFillPlacesTheExitAtOrigin:
    def test_buy_fill_places_a_reduce_only_sell_at_origin_and_keeps_the_short_entry(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 99.0)  # 買單成交 → +2
        assert runner.broker.position_qty() == QTY
        assert resting_detail(records) == [("Sell", pytest.approx(100.0), "exit", True),
                                           ("Sell", pytest.approx(101.0), "entry", False)]
        assert runner.state == RunState.IN_POSITION

    def test_sell_fill_places_a_reduce_only_buy_at_origin_and_keeps_the_long_entry(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 101.0)  # 賣單成交 → −2
        assert runner.broker.position_qty() == -QTY
        assert resting_detail(records) == [("Buy", pytest.approx(99.0), "entry", False),
                                           ("Buy", pytest.approx(100.0), "exit", True)]

    def test_the_other_side_is_not_canceled_and_nothing_stacks(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 99.0)
        runner.tick(at(2), 98.0)  # 繼續跌:沒有新單、沒有加碼
        assert len(resting(records)) == 2 and runner.broker.position_qty() == QTY


class TestExitFillRestoresTheInitialState:
    def test_long_returns_to_origin_closes_and_the_buy_entry_comes_back(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 99.0)  # +2
        runner.tick(at(2), 100.0)  # 回到起點:平倉單成交
        assert runner.broker.position_qty() == 0
        assert resting(records) == INITIAL  # 回到最初狀態
        [event] = runner.events
        assert event.direction == "long" and event.realized_pnl == pytest.approx((100.0 - 99.0) * QTY)
        assert runner.state == RunState.ENTRY_PENDING

    def test_short_returns_to_origin_closes_and_the_sell_entry_comes_back(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 101.0)  # −2
        runner.tick(at(2), 100.0)
        assert runner.broker.position_qty() == 0
        assert resting(records) == INITIAL
        [event] = runner.events
        assert event.direction == "short" and event.realized_pnl == pytest.approx((101.0 - 100.0) * QTY)

    def test_loops_without_a_cap_on_the_same_side(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        for i in range(6):  # 同一邊連做 6 輪:沒有「每邊每週末最多一筆」的限制
            runner.tick(at(1 + 2 * i), 99.0)
            runner.tick(at(2 + 2 * i), 100.0)
        assert [e.direction for e in runner.events] == ["long"] * 6
        assert resting(records) == INITIAL and runner.broker.position_qty() == 0

    def test_long_and_short_rounds_alternate_freely(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        for i, price in enumerate([99.0, 100.0, 101.0, 100.0, 99.0, 100.0], start=1):
            runner.tick(at(i), price)
        assert [e.direction for e in runner.events] == ["long", "short", "long"]
        assert all(e.realized_pnl == pytest.approx(1.0 * QTY) for e in runner.events)

    def test_price_in_between_does_nothing(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 99.5)
        runner.tick(at(2), 100.5)
        assert runner.broker.position_qty() == 0 and runner.events == []
        assert resting(records) == INITIAL


class TestJumpAcrossTheWholeBand:
    def test_long_exit_then_short_entry_in_one_tick(self):
        """持多單時價格一下子衝過起點到賣價:平倉單先成交(平掉多單,算在起點價),接著空單進場成交。"""
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 99.0)  # +2
        runner.tick(at(2), 101.0)  # 平倉單(100)跟空單進場(101)同一個 tick 都成交
        assert runner.broker.position_qty() == -QTY
        [event] = runner.events
        assert event.direction == "long" and event.realized_pnl == pytest.approx((100.0 - 99.0) * QTY)
        # 多單那邊的進場單補回去;空單的平倉單掛在起點
        assert resting_detail(records) == [("Buy", pytest.approx(99.0), "entry", False),
                                           ("Buy", pytest.approx(100.0), "exit", True)]


class TestExternalCancel:
    def test_canceled_entry_is_placed_again_at_the_same_price(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        buy = [r for r in records if r.side == "Buy"][0]
        runner.broker.cancel_order(buy.order_id)  # 例如在 Bybit App 手動取消
        runner.tick(at(1), 100.0)
        assert resting(records) == INITIAL

    def test_canceled_exit_is_placed_again_at_origin_while_the_position_is_held(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 99.0)  # +2,平倉單掛在 100
        exit_order = [r for r in records if r.purpose == "exit"][0]
        runner.broker.cancel_order(exit_order.order_id)
        runner.tick(at(2), 99.0)
        assert resting_detail(records) == [("Sell", pytest.approx(100.0), "exit", True),
                                           ("Sell", pytest.approx(101.0), "entry", False)]

    def test_canceled_exit_with_no_position_goes_back_to_the_initial_state(self):
        """平倉單被取消、而且部位已經不在了(例如手動平掉):不能再掛 reduce_only 的單,改把進場單補回去。"""
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 99.0)
        exit_order = [r for r in records if r.purpose == "exit"][0]
        runner.broker.cancel_order(exit_order.order_id)
        runner.broker.market_close(QTY)  # 手動把部位平掉
        runner.tick(at(2), 99.5)
        assert resting(records) == INITIAL


class TestEndOfWindow:
    def test_cleanup_cancels_everything_and_flattens(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 99.0)  # +2
        runner.request_stop()
        runner.tick(at(2), 99.5)
        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0
        assert resting(records) == []
        assert runner.events[-1].forced is True

    def test_cleanup_with_no_position_only_cancels_the_orders(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.request_stop()
        runner.tick(at(1), 100.0)
        assert runner.state == RunState.STOPPED and runner.broker.position_qty() == 0
        assert resting(records) == [] and runner.events == []

    def test_uses_origin_given_at_start(self):
        records = []
        runner = make(records, origin=200.0)
        runner.tick(at(0), 200.0)
        assert resting(records) == [("Buy", pytest.approx(198.0), QTY), ("Sell", pytest.approx(202.0), QTY)]


class TestHoldSeconds:
    def test_only_the_exit_fill_has_hold_seconds(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(5), 98.9)  # 買單成交 → +2
        runner.tick(at(30), 100.5)  # 平倉單成交
        buy = next(r for r in _latest(records) if r.side == "Buy" and r.purpose == "entry" and r.status == "closed")
        sell = next(r for r in _latest(records) if r.side == "Sell" and r.purpose == "exit" and r.status == "closed")
        assert buy.hold_seconds is None
        assert sell.hold_seconds == 25 * 60
