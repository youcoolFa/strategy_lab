from datetime import datetime, timezone

from strategy_lab.interfaces import StrategyContext
from strategy_lab.plugins.exit.bracket_tp_sl import BracketTPSLExit
from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit


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


class TestReturnToReferenceExit:
    def test_should_exit_is_always_true(self):
        plugin = ReturnToReferenceExit()
        assert plugin.should_exit(make_ctx(origin_price=1000.0)) is True

    def test_exit_price_is_origin_price(self):
        plugin = ReturnToReferenceExit()
        ctx = make_ctx(price=1200.0, origin_price=1000.0)
        assert plugin.exit_price(ctx) == 1000.0


class TestBracketTPSLExit:
    def test_no_exit_inside_band(self):
        plugin = BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5)
        ctx = make_ctx(price=1004.0, entry_price=1000.0)
        assert plugin.should_exit(ctx) is False

    def test_exit_on_take_profit(self):
        plugin = BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5)
        ctx = make_ctx(price=1010.0, entry_price=1000.0)
        assert plugin.should_exit(ctx) is True

    def test_exit_on_stop_loss(self):
        plugin = BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5)
        ctx = make_ctx(price=994.0, entry_price=1000.0)
        assert plugin.should_exit(ctx) is True

    def test_exit_price_is_current_price(self):
        plugin = BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5)
        assert plugin.exit_price(make_ctx(price=1010.0)) == 1010.0
