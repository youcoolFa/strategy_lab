"""event = 部位從 0 開始、回到 0 結束。由成交自動切出來,不用每個策略
自己定義;單筆進出(mean_reversion)跟分批加減碼(之後的分注法)同一套。"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.engine.events import EventTracker, summarize

T0 = datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc)


def t(minutes):
    return T0 + timedelta(minutes=minutes)


class TestSingleEntryExit:
    def test_long_round_trip_is_one_event_with_realized_pnl(self):
        tracker = EventTracker()
        assert tracker.on_fill(t(0), +1.0, 990.0) is None  # 開倉,event 還沒結束
        event = tracker.on_fill(t(30), -1.0, 1010.0)

        assert event.index == 1
        assert event.realized_pnl == pytest.approx(20.0)
        assert event.avg_entry == pytest.approx(990.0)
        assert event.avg_exit == pytest.approx(1010.0)
        assert event.direction == "long"
        assert event.fills == 2
        assert event.forced is False
        assert event.end_time - event.start_time == timedelta(minutes=30)

    def test_short_round_trip(self):
        tracker = EventTracker()
        tracker.on_fill(t(0), -1.0, 1010.0)
        event = tracker.on_fill(t(10), +1.0, 990.0)

        assert event.direction == "short"
        assert event.realized_pnl == pytest.approx(20.0)


class TestDrawdownInsideEvent:
    def test_max_drawdown_is_the_worst_unrealized_point_even_without_stop_loss(self):
        # 沒有停損 ≠ 沒有回撤:09-27 那一輪買在 84688,00:00 到過 84424.7。
        tracker = EventTracker()
        tracker.on_fill(t(0), +0.001, 84688.0)
        tracker.on_price(t(60), 84600.0)
        tracker.on_price(t(80), 84424.7)
        tracker.on_price(t(90), 84700.0)
        event = tracker.on_fill(t(120), -0.001, 84644.5, forced=True)

        assert event.max_drawdown == pytest.approx((84424.7 - 84688.0) * 0.001)
        assert event.realized_pnl == pytest.approx((84644.5 - 84688.0) * 0.001)
        assert event.forced is True

    def test_drawdown_is_zero_when_price_never_goes_against_the_position(self):
        tracker = EventTracker()
        tracker.on_fill(t(0), +1.0, 100.0)
        tracker.on_price(t(1), 101.0)
        event = tracker.on_fill(t(2), -1.0, 102.0)
        assert event.max_drawdown == 0.0

    def test_prices_outside_an_event_are_ignored(self):
        tracker = EventTracker()
        tracker.on_price(t(0), 1.0)  # 沒持倉
        tracker.on_fill(t(1), +1.0, 100.0)
        event = tracker.on_fill(t(2), -1.0, 100.0)
        assert event.max_drawdown == 0.0


class TestScaleInScaleOut:
    def test_multiple_fills_stay_one_event_until_flat(self):
        # 分注法:跌買 1、再跌買 2、再跌買 3,升賣 1、賣 2、賣 3,回到 0 才算一個 event
        tracker = EventTracker()
        assert tracker.on_fill(t(0), +1.0, 100.0) is None
        assert tracker.on_fill(t(1), +2.0, 98.0) is None
        assert tracker.on_fill(t(2), +3.0, 96.0) is None
        assert tracker.on_fill(t(3), -1.0, 99.0) is None
        assert tracker.on_fill(t(4), -2.0, 100.0) is None
        event = tracker.on_fill(t(5), -3.0, 101.0)

        avg = (100 * 1 + 98 * 2 + 96 * 3) / 6
        assert event.avg_entry == pytest.approx(avg)
        assert event.realized_pnl == pytest.approx((99 - avg) * 1 + (100 - avg) * 2 + (101 - avg) * 3)
        assert event.max_position == pytest.approx(6.0)
        assert event.fills == 6


class TestSummarize:
    def test_totals_and_max_drawdown_across_events(self):
        tracker = EventTracker()
        events = []
        tracker.on_fill(t(0), +1.0, 100.0)
        events.append(tracker.on_fill(t(1), -1.0, 110.0))  # +10,累積 10
        tracker.on_fill(t(2), +1.0, 100.0)
        tracker.on_price(t(3), 85.0)  # 帳面 -15 → 權益從高點 10 掉到 -5
        events.append(tracker.on_fill(t(4), -1.0, 95.0))  # -5,累積 5
        tracker.on_fill(t(5), +1.0, 100.0)
        events.append(tracker.on_fill(t(6), -1.0, 103.0))  # +3,累積 8

        s = summarize(events)

        assert s.count == 3
        assert s.total_pnl == pytest.approx(8.0)
        assert s.wins == 2
        assert s.max_drawdown == pytest.approx(-15.0)  # 高點 10 → 最低 -5

    def test_empty(self):
        s = summarize([])
        assert s.count == 0 and s.total_pnl == 0.0 and s.max_drawdown == 0.0
