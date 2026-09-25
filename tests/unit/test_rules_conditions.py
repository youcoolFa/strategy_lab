"""rules/conditions.py 裡每個 primitive 的真值表測試。"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.interfaces import StrategyContext
from strategy_lab.rules.conditions import (
    MaxDurationElapsed,
    MovingAverageCross,
    PriceAboveReference,
    PriceAtOrAboveReference,
    PriceAtOrBelowReference,
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


class TestPriceAboveReference:
    """PriceBelowReference 的鏡像版本,給開空倉用:現價漲破
    origin_price 的 deviation_pct% 就觸發(做空進場)。"""

    def test_true_when_price_at_or_above_target(self):
        condition = PriceAboveReference(deviation_pct=1.0)
        assert condition.evaluate(make_ctx(price=1010.0, origin_price=1000.0)) is True

    def test_false_when_price_below_target(self):
        condition = PriceAboveReference(deviation_pct=1.0)
        assert condition.evaluate(make_ctx(price=1005.0, origin_price=1000.0)) is False


class TestPriceAtOrBelowReference:
    """PriceAtOrAboveReference 的鏡像版本,給平空倉用:現價回到(或跌破)
    origin_price 就觸發(回補出場)。"""

    def test_true_when_price_at_or_below_origin(self):
        condition = PriceAtOrBelowReference()
        assert condition.evaluate(make_ctx(price=1000.0, origin_price=1000.0)) is True

    def test_false_when_price_above_origin(self):
        condition = PriceAtOrBelowReference()
        assert condition.evaluate(make_ctx(price=1001.0, origin_price=1000.0)) is False


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
    """用「連續 hours 小時」的滾動視窗,不是日曆日——日曆日版本需要
    TimeWindow 撐過至少 N+1 個日期邊界才可能觸發,`WeeklyWindow` 預設
    只有約 50 小時,連續 3 個日曆日永遠撐不到(見
    docs/ARCHITECTURE.md)。改成小時為單位後不再有這種隱性下限。"""

    def _tick(self, hours_offset: float, price: float) -> StrategyContext:
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return make_ctx(now=base + timedelta(hours=hours_offset), price=price)

    def test_requires_both_margin_pct_and_margin_fixed_to_be_exclusive(self):
        with pytest.raises(ValueError):
            SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0)
        with pytest.raises(ValueError):
            SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, margin_pct=5.0, margin_fixed=1.0)

    def test_false_before_observation_window_is_covered(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, hours=6.0, margin_pct=5.0)
        assert condition.evaluate(self._tick(0, 103.0)) is False
        assert condition.evaluate(self._tick(2, 105.0)) is False
        assert condition.evaluate(self._tick(5, 106.0)) is False  # 還沒滿 6 小時

    def test_true_once_window_covered_all_above_threshold_and_average_above_margin(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, hours=6.0, margin_pct=5.0)
        condition.evaluate(self._tick(0, 103.0))
        condition.evaluate(self._tick(3, 105.0))
        assert condition.evaluate(self._tick(6, 106.0)) is True  # 剛好滿 6 小時

    def test_false_when_any_observation_in_window_is_at_or_below_threshold(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, hours=6.0, margin_pct=5.0)
        condition.evaluate(self._tick(0, 103.0))
        condition.evaluate(self._tick(3, 99.0))  # 這一筆沒超過門檻
        assert condition.evaluate(self._tick(6, 106.0)) is False

    def test_false_when_average_below_margin(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, hours=6.0, margin_pct=20.0)
        condition.evaluate(self._tick(0, 103.0))
        condition.evaluate(self._tick(3, 105.0))
        # 平均約 104.67,目標 90*1.2=108
        assert condition.evaluate(self._tick(6, 106.0)) is False

    def test_margin_fixed_mode(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, hours=6.0, margin_fixed=15.0)
        condition.evaluate(self._tick(0, 103.0))
        condition.evaluate(self._tick(3, 105.0))
        # 目標 90+15=105,平均約 104.67 沒超過
        assert condition.evaluate(self._tick(6, 106.0)) is False

    def test_old_observations_outside_the_window_no_longer_count(self):
        """滾動視窗:很久以前一筆低於門檻的觀察,一旦滑出視窗之外,不該
        繼續拖累後面的判斷。"""
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, hours=6.0, margin_pct=5.0)
        condition.evaluate(self._tick(0, 50.0))  # 很低,但之後會滑出視窗
        condition.evaluate(self._tick(10, 103.0))
        condition.evaluate(self._tick(13, 105.0))
        assert condition.evaluate(self._tick(16, 106.0)) is True  # 視窗是 [10,16],t=0 已經滑出去了


class TestSustainedPriceBreakoutTimeUnits:
    """`hours` 是內部唯一表示,但 YAML/呼叫端不一定想自己換算成小數小時
    ——`minutes`/`days` 是方便輸入用的替代參數,三者互斥(跟
    margin_pct/margin_fixed 同樣的「恰好給一個」模式),換算後效果要
    跟直接寫等值的 hours 完全一樣。不支援 months/years:日曆月、年的
    長度不固定,會重新引入「日曆邊界」那類問題(見
    docs/ARCHITECTURE.md §4.6.1)。"""

    def _tick(self, hours_offset: float, price: float) -> StrategyContext:
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return make_ctx(now=base + timedelta(hours=hours_offset), price=price)

    def test_days_param_converts_to_equivalent_hours_window(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, days=0.25, margin_pct=5.0)
        condition.evaluate(self._tick(0, 103.0))
        assert condition.evaluate(self._tick(5, 106.0)) is False  # 0.25 天 = 6 小時,還沒滿
        assert condition.evaluate(self._tick(6, 106.0)) is True

    def test_minutes_param_converts_to_equivalent_hours_window(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, minutes=360.0, margin_pct=5.0)
        condition.evaluate(self._tick(0, 103.0))
        assert condition.evaluate(self._tick(5, 106.0)) is False  # 360 分鐘 = 6 小時,還沒滿
        assert condition.evaluate(self._tick(6, 106.0)) is True

    def test_default_with_no_unit_given_is_still_seventy_two_hours(self):
        condition = SustainedPriceBreakout(threshold_price=100.0, reference_price=90.0, margin_pct=5.0)
        assert condition.window_duration == timedelta(hours=72.0)

    def test_more_than_one_unit_given_raises(self):
        with pytest.raises(ValueError):
            SustainedPriceBreakout(
                threshold_price=100.0, reference_price=90.0, hours=6.0, days=1.0, margin_pct=5.0
            )
