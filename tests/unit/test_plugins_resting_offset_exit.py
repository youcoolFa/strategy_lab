"""weekend_band_reversion 的平倉 plugin:平倉點不是 origin,而是往獲利
方向再偏 offset_pct%——long 在 origin 上方,short 在 origin 下方。"""

from datetime import datetime, timezone

import pytest

from strategy_lab.interfaces import StrategyContext
from strategy_lab.plugins.exit.resting_offset_from_reference import RestingOffsetFromReferenceExit
from strategy_lab.registry import get
from strategy_lab.rules.conditions import AlwaysTrue


def make_ctx(direction="long", origin_price=1000.0):
    return StrategyContext(
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        price=origin_price,
        origin_price=origin_price,
        direction=direction,
    )


class TestRestingOffsetFromReferenceExit:
    def test_long_exit_is_above_origin(self):
        plugin = RestingOffsetFromReferenceExit(offset_pct=0.75)
        assert plugin.exit_price(make_ctx(direction="long")) == pytest.approx(1007.5)

    def test_short_exit_is_below_origin(self):
        plugin = RestingOffsetFromReferenceExit(offset_pct=0.75)
        assert plugin.exit_price(make_ctx(direction="short")) == pytest.approx(992.5)

    def test_zero_offset_is_the_same_as_returning_to_origin(self):
        plugin = RestingOffsetFromReferenceExit(offset_pct=0.0)
        assert plugin.exit_price(make_ctx(direction="long")) == 1000.0
        assert plugin.exit_price(make_ctx(direction="short")) == 1000.0

    def test_rule_is_always_true_so_exit_is_placed_right_after_entry_fills(self):
        assert isinstance(RestingOffsetFromReferenceExit(offset_pct=0.75).rule, AlwaysTrue)

    def test_is_marked_resting_so_market_order_type_is_rejected(self):
        assert RestingOffsetFromReferenceExit.resting is True

    def test_registered_under_yaml_type_name(self):
        assert get("exit", "resting_offset_from_reference") is RestingOffsetFromReferenceExit
