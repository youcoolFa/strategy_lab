"""rules/conditions.py 裡每個 primitive 的真值表測試。"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.interfaces import StrategyContext
from strategy_lab.rules.conditions import (
    MaxDurationElapsed,
    MovingAverageCross,
    PriceAtOrAboveReference,
    PriceBelowReference,
    PriceChangeFromEntry,
    SustainedPriceBreakout,
    simple_moving_average,
)


def make_ctx(**overrides):
    base = dict(
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        price=100.0,
        price_history=(),
        origin_price=None,
        entry_price=None,
        position_qty=0.0,
        entry_time=None,
    )
    base.update(overrides)
    return StrategyContext(**base)


class TestSimpleMovingAverage:
    def test_not_enough_data_returns_none(self):
        assert simple_moving_average([1, 2], 3) is None

    def test_average_of_last_n(self):
        assert simple_moving_average([1, 2, 3, 4, 5], 3) == 4.0


class TestPriceBelowReference:
    def test_true_when_price_at_or_below_target(self):
        condition = PriceBelowReference(deviation_pct=1.0)
        assert condition.evaluate(make_ctx(price=990.0, origin_price=1000.0)) is True

    def test_false_when_price_above_target(self):
        condition = PriceBelowReference(deviation_pct=1.0)
        assert condition.evaluate(make_ctx(price=995.0, origin_price=1000.0)) is False


class TestPriceAtOrAboveReference:
    def test_true_when_price_at_or_above_origin(self):
        condition = PriceAtOrAboveReference()
        assert condition.evaluate(make_ctx(price=1000.0, origin_price=1000.0)) is True

    def test_false_when_price_below_origin(self):
        condition = PriceAtOrAboveReference()
        assert condition.evaluate(make_ctx(price=999.0, origin_price=1000.0)) is False


class TestMovingAverageCross:
    def test_no_cross_without_enough_history(self):
        condition = MovingAverageCross(fast_window=2, slow_window=4)
        assert condition.evaluate(make_ctx(price_history=(1, 2, 3))) is False

    def test_true_on_fresh_upward_crossover(self):
        condition = MovingAverageCross(fast_window=2, slow_window=4)
        # fast_prev == slow_prev == 10(持平),當下這一筆價格跳升讓
        # fast_now > slow_now,形成剛發生的向上交叉。
        ctx = make_ctx(price_history=(10, 10, 10, 10, 20))
        assert condition.evaluate(ctx) is True

    def test_false_when_flat_no_crossover(self):
        condition = MovingAverageCross(fast_window=2, slow_window=4)
        ctx = make_ctx(price_history=(20, 20, 20, 20, 20))
        assert condition.evaluate(ctx) is False


class TestPriceChangeFromEntry:
    def test_up_direction_triggers_at_threshold(self):
        condition = PriceChangeFromEntry(threshold_pct=1.0, direction="up")
        assert condition.evaluate(make_ctx(price=1010.0, entry_price=1000.0)) is True
        assert condition.evaluate(make_ctx(price=1005.0, entry_price=1000.0)) is False

    def test_down_direction_triggers_at_threshold(self):
        condition = PriceChangeFromEntry(threshold_pct=0.5, direction="down")
        assert condition.evaluate(make_ctx(price=994.0, entry_price=1000.0)) is True
        assert condition.evaluate(make_ctx(price=997.0, entry_price=1000.0)) is False


class TestMaxDurationElapsed:
    def test_true_after_max_minutes(self):
        condition = MaxDurationElapsed(max_minutes=30)
        entry_time = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        now = entry_time + timedelta(minutes=31)
        assert condition.evaluate(make_ctx(now=now, entry_time=entry_time)) is True

    def test_false_before_max_minutes(self):
        condition = MaxDurationElapsed(max_minutes=30)
        entry_time = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        now = entry_time + timedelta(minutes=29)
        assert condition.evaluate(make_ctx(now=now, entry_time=entry_time)) is False

    def test_false_when_no_entry_time(self):
        condition = MaxDurationElapsed(max_minutes=30)
        assert condition.evaluate(make_ctx(entry_time=None)) is False


class TestSustainedPriceBreakout:
    def _tick(self, day: int, price: float) -> StrategyContext:
        return make_ctx(now=datetime(2026, 1, day, tzinfo=timezone.utc), price=price)

    def test_requires_both_margin_pct_and_margin_fixed_to_be_exclusive(self):
        with pytest.raises(ValueError):
            SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0)
        with pytest.raises(ValueError):
            SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, margin_pct=5.0, margin_fixed=1.0)

    def test_false_while_fewer_than_days_have_finalized(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, days=3, margin_pct=5.0)
        assert condition.evaluate(self._tick(1, 103.0)) is False
        assert condition.evaluate(self._tick(2, 105.0)) is False
        assert condition.evaluate(self._tick(3, 106.0)) is False  # 第3天還沒結束,只結算了 day1/day2

    def test_true_once_days_finalized_all_above_threshold_and_average_above_margin(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, days=3, margin_pct=5.0)
        for day, price in [(1, 103.0), (2, 105.0), (3, 106.0)]:
            condition.evaluate(self._tick(day, price))
        # day4 第一筆 tick 才會把 day3 的收盤(106.0)結算進去
        assert condition.evaluate(self._tick(4, 999.0)) is True

    def test_false_when_not_every_day_above_threshold(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, days=3, margin_pct=5.0)
        for day, price in [(1, 103.0), (2, 99.0), (3, 106.0)]:  # day2 收盤沒超過門檻
            condition.evaluate(self._tick(day, price))
        assert condition.evaluate(self._tick(4, 999.0)) is False

    def test_false_when_average_below_margin(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, days=3, margin_pct=20.0)
        for day, price in [(1, 103.0), (2, 105.0), (3, 106.0)]:  # 平均 104.67,目標 90*1.2=108
            condition.evaluate(self._tick(day, price))
        assert condition.evaluate(self._tick(4, 999.0)) is False

    def test_margin_fixed_mode(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, days=3, margin_fixed=15.0)
        for day, price in [(1, 103.0), (2, 105.0), (3, 106.0)]:  # 平均 104.67,目標 90+15=105
            condition.evaluate(self._tick(day, price))
        assert condition.evaluate(self._tick(4, 999.0)) is False
