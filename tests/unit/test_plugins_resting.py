"""sat_strategy 機制的進出場 plugin:一啟動就掛限價單,不等價格先越過門檻。
方向(long/short)從 ctx.direction 讀,同一對 plugin 兩個方向共用。"""

from datetime import datetime, timezone

import pytest

from strategy_lab.interfaces import StrategyContext
from strategy_lab.plugins.entry.resting_deviation_from_reference import (
    RestingDeviationFromReferenceEntry,
    compute_resting_entry_price,
)
from strategy_lab.plugins.exit.resting_return_to_reference import RestingReturnToReferenceExit
from strategy_lab.registry import get
from strategy_lab.rules.conditions import AlwaysTrue


def make_ctx(price=1000.0, origin_price=1000.0, direction="long"):
    return StrategyContext(
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        price=price,
        origin_price=origin_price,
        direction=direction,
    )


class TestComputeRestingEntryPrice:
    def test_long_is_below_origin(self):
        assert compute_resting_entry_price(1000.0, 0.75, "long") == 992.5

    def test_short_is_above_origin(self):
        assert compute_resting_entry_price(1000.0, 0.75, "short") == pytest.approx(1007.5)

    def test_does_not_round_to_two_decimals(self):
        # sat_strategy 的 round(..., 2) 只適合 BTC 這種價位;WLD(約 0.48)
        # 四捨五入到兩位小數會把 0.75% 扭成 -1.5% 或 +0.2%。精度改由
        # LiveBroker 依交易所真實 tickSize 修正。
        assert abs(compute_resting_entry_price(0.4772, 0.75, "long") - 0.473621) < 1e-9


class TestRestingDeviationFromReferenceEntry:
    def test_rule_is_always_true_so_order_is_placed_immediately(self):
        plugin = RestingDeviationFromReferenceEntry(deviation_pct=0.75)
        assert isinstance(plugin.rule, AlwaysTrue)
        assert plugin.rule.evaluate(make_ctx(price=1000.0)) is True

    def test_entry_price_follows_ctx_direction(self):
        plugin = RestingDeviationFromReferenceEntry(deviation_pct=0.75)
        assert plugin.entry_price(make_ctx(direction="long")) == 992.5
        assert plugin.entry_price(make_ctx(direction="short")) == pytest.approx(1007.5)

    def test_is_marked_resting(self):
        assert RestingDeviationFromReferenceEntry.resting is True

    def test_registered_under_yaml_type_name(self):
        assert get("entry", "resting_deviation_from_reference") is RestingDeviationFromReferenceEntry


class TestRestingReturnToReferenceExit:
    def test_rule_is_always_true(self):
        plugin = RestingReturnToReferenceExit()
        assert isinstance(plugin.rule, AlwaysTrue)

    def test_exit_price_is_origin_for_both_directions(self):
        plugin = RestingReturnToReferenceExit()
        assert plugin.exit_price(make_ctx(direction="long")) == 1000.0
        assert plugin.exit_price(make_ctx(direction="short")) == 1000.0

    def test_is_marked_resting(self):
        assert RestingReturnToReferenceExit.resting is True

    def test_registered_under_yaml_type_name(self):
        assert get("exit", "resting_return_to_reference") is RestingReturnToReferenceExit


class TestAlwaysTrue:
    def test_evaluates_true_regardless_of_price(self):
        assert AlwaysTrue().evaluate(make_ctx(price=1.0)) is True
        assert AlwaysTrue().evaluate(make_ctx(price=999999.0)) is True


class TestStrategyContextDirection:
    def test_defaults_to_long(self):
        ctx = StrategyContext(now=datetime(2026, 1, 1, tzinfo=timezone.utc), price=1.0)
        assert ctx.direction == "long"
