"""區間 runner(engine/band_runner.py,2026-10-10 weekend_band_reversion 改版;PaperBroker,不碰網路):
- 一開始上下各掛一張:買 = origin × (1 − buy_pct%)、賣 = origin × (1 + sell_pct%)
- 每成交一張,就在對面價位補一張同數量的單(單向持倉:買賣是淨部位的加減)
- 第一張成交後,持倉在 +1 / −1 之間切換;每次穿過整個區間賺一次價差
- 直到時間窗結束:取消所有掛單、市價平倉
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


def resting(records):
    state = {}
    for r in records:
        state[r.order_id] = r
    return sorted((r.side, r.price, r.qty) for r in state.values() if r.status == "open")


class TestStart:
    def test_first_tick_places_one_buy_below_and_one_sell_above(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        assert resting(records) == [("Buy", pytest.approx(99.0), QTY), ("Sell", pytest.approx(101.0), QTY)]
        assert all(r.purpose == "band" and r.reduce_only is False for r in records)
        assert runner.state == RunState.ENTRY_PENDING

    def test_not_a_band_entry_is_rejected(self):
        from strategy_lab.plugins.entry.scale_in import ScaleInEntry

        with pytest.raises(ValueError, match="band"):
            BandRunner(entry=ScaleInEntry(weights=[1]), exit=BandExit(), time_window=WeeklyWindow(), order_qty=1.0)


class TestFillPlacesTheOppositeOrder:
    def test_buy_fill_adds_a_second_sell_on_the_other_side(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 99.0)  # 買單成交 → 持倉 +2
        assert runner.broker.position_qty() == QTY
        assert resting(records) == [("Sell", pytest.approx(101.0), QTY), ("Sell", pytest.approx(101.0), QTY)]
        assert runner.state == RunState.IN_POSITION

    def test_crossing_the_band_flips_long_to_short_and_books_the_spread(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 99.0)  # +2
        runner.tick(at(2), 101.0)  # 兩張賣單成交:平多 + 開空 → −2
        assert runner.broker.position_qty() == -QTY
        assert resting(records) == [("Buy", pytest.approx(99.0), QTY), ("Buy", pytest.approx(99.0), QTY)]
        [event] = runner.events
        assert event.direction == "long" and event.realized_pnl == pytest.approx((101.0 - 99.0) * QTY)

    def test_keeps_cycling_between_plus_and_minus_one(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        for i, price in enumerate([99.0, 101.0, 99.0, 101.0, 99.0], start=1):
            runner.tick(at(i), price)
        assert runner.broker.position_qty() == QTY
        assert [e.direction for e in runner.events] == ["long", "short", "long", "short"]
        assert all(e.realized_pnl == pytest.approx(2.0 * QTY) for e in runner.events)

    def test_sell_first_mirrors(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 101.0)  # 賣單先成交 → −2,對面補一張買單
        assert runner.broker.position_qty() == -QTY
        assert resting(records) == [("Buy", pytest.approx(99.0), QTY), ("Buy", pytest.approx(99.0), QTY)]

    def test_price_in_between_does_nothing(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        runner.tick(at(1), 99.5)
        runner.tick(at(2), 100.5)
        assert runner.broker.position_qty() == 0 and runner.events == []
        assert len(resting(records)) == 2

    def test_externally_canceled_order_is_placed_again(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 100.0)
        buy = [r for r in records if r.side == "Buy"][0]
        runner.broker.cancel_order(buy.order_id)  # 例如在 Bybit App 手動取消
        runner.tick(at(1), 100.0)
        assert resting(records) == [("Buy", pytest.approx(99.0), QTY), ("Sell", pytest.approx(101.0), QTY)]


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

    def test_uses_origin_given_at_start(self):
        records = []
        runner = make(records, origin=200.0)
        runner.tick(at(0), 200.0)
        assert resting(records) == [("Buy", pytest.approx(198.0), QTY), ("Sell", pytest.approx(202.0), QTY)]
