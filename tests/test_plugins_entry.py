from datetime import datetime, timezone

from strategy_lab.interfaces import StrategyContext
from strategy_lab.plugins.entry.deviation_from_reference import DeviationFromReferenceEntry, compute_entry_price
from strategy_lab.plugins.entry.ma_crossover import MACrossoverEntry, simple_moving_average


def make_ctx(**overrides):
    base = dict(
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        price=100.0,
        price_history=(),
        origin_price=None,
        entry_price=None,
        position_qty=0.0,
    )
    base.update(overrides)
    return StrategyContext(**base)


class TestComputeEntryPrice:
    def test_deviation_below_reference(self):
        assert compute_entry_price(1000.0, 0.75) == 992.5

    def test_zero_deviation_returns_reference(self):
        assert compute_entry_price(1000.0, 0.0) == 1000.0


class TestDeviationFromReferenceEntry:
    def test_should_enter_is_always_true(self):
        plugin = DeviationFromReferenceEntry(deviation_pct=0.75)
        assert plugin.should_enter(make_ctx(origin_price=1000.0)) is True

    def test_entry_price_uses_origin_price_not_current_price(self):
        plugin = DeviationFromReferenceEntry(deviation_pct=0.75)
        ctx = make_ctx(price=1200.0, origin_price=1000.0)
        assert plugin.entry_price(ctx) == compute_entry_price(1000.0, 0.75)


class TestSimpleMovingAverage:
    def test_not_enough_data_returns_none(self):
        assert simple_moving_average([1, 2], 3) is None

    def test_average_of_last_n(self):
        assert simple_moving_average([1, 2, 3, 4, 5], 3) == 4.0


class TestMACrossoverEntry:
    def test_no_entry_without_enough_history(self):
        plugin = MACrossoverEntry(fast_window=2, slow_window=4)
        assert plugin.should_enter(make_ctx(price_history=(1, 2, 3))) is False

    def test_enters_on_fresh_upward_crossover(self):
        plugin = MACrossoverEntry(fast_window=2, slow_window=4)
        # fast_prev == slow_prev == 10 (flat), current price jump makes fast_now > slow_now
        ctx = make_ctx(price_history=(10, 10, 10, 10, 20))
        assert plugin.should_enter(ctx) is True

    def test_no_entry_when_flat_no_crossover(self):
        plugin = MACrossoverEntry(fast_window=2, slow_window=4)
        ctx = make_ctx(price_history=(20, 20, 20, 20, 20))
        assert plugin.should_enter(ctx) is False

    def test_entry_price_is_current_price(self):
        plugin = MACrossoverEntry()
        assert plugin.entry_price(make_ctx(price=123.45)) == 123.45
