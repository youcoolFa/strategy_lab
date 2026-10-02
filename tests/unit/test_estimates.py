"""啟動前估算:plugin 自己描述價位(planned_exit),計算元件(metric)只讀
這些事實、不寫死任何策略,組合起來就是每個策略各自正確的公式。"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.estimates.model import Estimate, MarketSnapshot
from strategy_lab.estimates.plan import build_order_plan
from strategy_lab.estimates.metrics import DEFAULT_METRICS, run_metrics
from strategy_lab.interfaces import PlannedExit
from strategy_lab.plugins.entry.ma_crossover import MACrossoverEntry
from strategy_lab.plugins.entry.resting_deviation_from_reference import RestingDeviationFromReferenceEntry
from strategy_lab.plugins.exit.bracket_tp_sl import BracketTPSLExit
from strategy_lab.plugins.exit.max_hold_duration import MaxHoldDurationExit
from strategy_lab.plugins.exit.resting_offset_from_reference import RestingOffsetFromReferenceExit
from strategy_lab.plugins.exit.resting_return_to_reference import RestingReturnToReferenceExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow
from strategy_lab.registry import get

NOW = datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc)
CLEANUP = NOW + timedelta(hours=49, minutes=55)


def market(price=1005.0, equity=500.0):
    return MarketSnapshot(
        symbol="BTCUSDT", price=price, equity=equity, maker_fee_rate=0.0002, taker_fee_rate=0.00055,
        leverage=100.0, margin_mode="REGULAR_MARGIN", now=NOW,
    )


def band_plan(direction="long", price=1005.0):
    return build_order_plan(
        strategy_name="weekend_band_reversion",
        entry=RestingDeviationFromReferenceEntry(deviation_pct=1.0),
        exit=RestingOffsetFromReferenceExit(offset_pct=1.0),
        direction=direction, origin_price=1000.0, origin_source="手動輸入", qty=1.0,
        market=market(price), cleanup_at=CLEANUP,
    )


def rows(results):
    return {row.key: row for result in results for row in result.rows}


class TestPluginsDescribeTheirOwnExitLevels:
    def test_resting_return_to_reference_takes_profit_at_origin_no_stop(self):
        from strategy_lab.interfaces import StrategyContext

        ctx = StrategyContext(now=NOW, price=1000.0, origin_price=1000.0, entry_price=990.0)
        assert RestingReturnToReferenceExit().planned_exit(ctx) == PlannedExit(take_profit=1000.0, stop_loss=None)

    def test_bracket_has_both_take_profit_and_stop_loss(self):
        from strategy_lab.interfaces import StrategyContext

        ctx = StrategyContext(now=NOW, price=1000.0, entry_price=1000.0)
        levels = BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5).planned_exit(ctx)
        assert levels.take_profit == pytest.approx(1010.0)
        assert levels.stop_loss == pytest.approx(995.0)

    def test_time_based_exit_has_no_price_levels(self):
        from strategy_lab.interfaces import StrategyContext

        ctx = StrategyContext(now=NOW, price=1000.0, entry_price=1000.0)
        assert MaxHoldDurationExit(max_minutes=60).planned_exit(ctx) == PlannedExit(None, None)


class TestBuildOrderPlan:
    def test_band_long_entry_below_origin_and_take_profit_above(self):
        plan = band_plan("long")
        assert plan.entry_price == pytest.approx(990.0)
        assert plan.take_profit == pytest.approx(1010.0)
        assert plan.stop_loss is None
        assert plan.entry_known_in_advance is True
        assert plan.entry_crosses_market is False  # 990 < 現價 1005,會掛著等

    def test_stale_origin_makes_entry_cross_the_market(self):
        # 2026-09-27 兩次實盤:origin 比現價高,買單一掛出去就吃單成交。
        plan = band_plan("long", price=980.0)
        assert plan.entry_crosses_market is True

    def test_band_short_is_mirrored(self):
        plan = band_plan("short", price=1000.0)
        assert plan.entry_price == pytest.approx(1010.0)
        assert plan.take_profit == pytest.approx(990.0)
        assert plan.entry_crosses_market is False

    def test_signal_based_entry_uses_current_price_and_is_flagged(self):
        plan = build_order_plan(
            strategy_name="ma_crossover_bracket",
            entry=MACrossoverEntry(fast_window=5, slow_window=20),
            exit=BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5),
            direction="long", origin_price=1000.0, origin_source="啟動當下即時價", qty=1.0,
            market=market(price=1000.0), cleanup_at=CLEANUP,
        )
        assert plan.entry_known_in_advance is False
        assert plan.entry_price == pytest.approx(1000.0)
        assert plan.take_profit == pytest.approx(1010.0)
        assert plan.stop_loss == pytest.approx(995.0)


class TestMetricRegistry:
    def test_default_metrics_are_registered_components(self):
        assert DEFAULT_METRICS == ["order_plan", "cycle_pnl", "risk", "time_window"]
        for name in DEFAULT_METRICS:
            assert get("metric", name) is not None


class TestCyclePnl:
    def test_resting_band_long_net_after_maker_fees(self):
        r = rows(run_metrics(Estimate(plan=band_plan("long"), market=market())))
        assert r["gross_pnl"].value == pytest.approx(20.0)  # 990 買 → 1010 賣
        assert r["fees"].value == pytest.approx(990 * 0.0002 + 1010 * 0.0002)
        assert r["net_pnl"].value == pytest.approx(20.0 - 0.4)

    def test_entry_crossing_market_pays_taker_fee_and_warns(self):
        r = rows(run_metrics(Estimate(plan=band_plan("long", price=980.0), market=market(price=980.0))))
        assert r["fees"].value == pytest.approx(990 * 0.00055 + 1010 * 0.0002)
        assert r["entry_crosses_market"].warning is True

    def test_short_profit_is_positive_when_buying_back_lower(self):
        r = rows(run_metrics(Estimate(plan=band_plan("short", price=1000.0), market=market(price=1000.0))))
        assert r["gross_pnl"].value == pytest.approx(20.0)


class TestRisk:
    def test_effective_leverage_is_notional_over_equity(self):
        r = rows(run_metrics(Estimate(plan=band_plan("long"), market=market(equity=500.0))))
        assert r["effective_leverage"].value == pytest.approx(990 / 500)

    def test_no_stop_loss_is_flagged_as_unbounded(self):
        r = rows(run_metrics(Estimate(plan=band_plan("long"), market=market())))
        assert r["max_loss"].warning is True
        assert r["max_loss"].value is None

    def test_adverse_scenarios_include_price_loss_and_fees(self):
        r = rows(run_metrics(Estimate(plan=band_plan("long"), market=market())))
        exit_price = 990 * 0.99
        expected = (exit_price - 990) - (990 * 0.0002 + exit_price * 0.00055)
        assert r["scenario_1pct"].value == pytest.approx(expected)

    def test_equity_wipeout_move(self):
        r = rows(run_metrics(Estimate(plan=band_plan("long"), market=market(equity=500.0))))
        assert r["equity_wipeout_move"].value == pytest.approx(500 / 990 * 100)

    def test_stop_loss_bounds_max_loss(self):
        plan = build_order_plan(
            strategy_name="ma_crossover_bracket",
            entry=MACrossoverEntry(fast_window=5, slow_window=20),
            exit=BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5),
            direction="long", origin_price=1000.0, origin_source="啟動當下即時價", qty=1.0,
            market=market(price=1000.0), cleanup_at=CLEANUP,
        )
        r = rows(run_metrics(Estimate(plan=plan, market=market(price=1000.0))))
        expected = (995.0 - 1000.0) - (1000 * 0.00055 + 995 * 0.00055)
        assert r["max_loss"].value == pytest.approx(expected)
        assert r["max_loss"].warning is False


class TestTimeWindow:
    def test_shows_cleanup_time_and_funding_settlements_inside_window(self):
        r = rows(run_metrics(Estimate(plan=band_plan("long"), market=market())))
        assert r["cleanup_at"].text.startswith("2026-10-05")
        # 04:00 UTC 起 ~50 小時,經過 08:00/16:00/00:00 UTC 的資金費結算
        assert r["funding_settlements"].value == 6

    def test_cleanup_in_the_past_warns(self):
        plan = band_plan("long")
        plan.cleanup_at = NOW - timedelta(minutes=1)
        r = rows(run_metrics(Estimate(plan=plan, market=market())))
        assert r["cleanup_at"].warning is True


class TestLoopInEstimate:
    def test_loop_count_is_shown(self):
        plan = band_plan("long")
        plan.loop = 2
        r = rows(run_metrics(Estimate(plan=plan, market=market())))
        assert r["loop"].value == 3
        assert "3" in r["loop"].text

    def test_unlimited_loop_is_shown(self):
        plan = band_plan("long")
        plan.loop = None
        r = rows(run_metrics(Estimate(plan=plan, market=market())))
        assert r["loop"].value is None
        assert "不限" in r["loop"].text
