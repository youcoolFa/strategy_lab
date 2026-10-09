"""區間策略(2026-10-10)的啟動前估算:上下兩個價位、每次穿過區間的損益、風險。"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.estimates.model import MarketSnapshot, OrderPlan

NOW = datetime(2026, 10, 10, 4, 0, tzinfo=timezone.utc)


def market(price=100.0, equity=500.0):
    return MarketSnapshot(symbol="XRPUSDT", price=price, equity=equity, maker_fee_rate=0.0002,
                          taker_fee_rate=0.00055, leverage=10.0, margin_mode="REGULAR_MARGIN", now=NOW)


class TestOrderPlanBandFields:
    def test_band_sell_price_marks_a_band_plan(self):
        common = dict(strategy_name="x", direction="long", origin_price=100.0, origin_source="s", qty=1.0,
                      entry_price=99.0, entry_known_in_advance=True, entry_crosses_market=False, take_profit=101.0,
                      stop_loss=None, exit_resting=True, cleanup_at=NOW)
        assert OrderPlan(**common).band is False
        plan = OrderPlan(**common, band_sell_price=101.0)
        assert plan.band is True and plan.band_sell_price == 101.0


def band_plan(price=100.0, origin=100.0, buy_pct=1.0, sell_pct=2.0, qty=2.0):
    from strategy_lab.estimates.plan import build_band_plan
    from strategy_lab.plugins.entry.band import BandEntry

    return build_band_plan(strategy_name="weekend_band_reversion", entry=BandEntry(buy_pct=buy_pct, sell_pct=sell_pct),
                           origin_price=origin, origin_source="啟動當下即時價", qty=qty, market=market(price),
                           cleanup_at=NOW + timedelta(days=2))


class TestBuildBandPlan:
    def test_buy_below_sell_above_origin(self):
        plan = band_plan()
        assert plan.band is True and plan.direction == "long"  # 每輪損益照 買價 → 賣價 算
        assert plan.entry_price == pytest.approx(99.0) and plan.band_sell_price == pytest.approx(102.0)
        assert plan.take_profit == pytest.approx(102.0) and plan.qty == 2.0
        assert plan.exit_resting is True and plan.stop_loss is None and plan.loop is None
        assert plan.entry_crosses_market is False

    @pytest.mark.parametrize("price", [98.0, 103.0])
    def test_price_already_outside_the_band_is_flagged(self, price):
        assert band_plan(price=price).entry_crosses_market is True


def metric(name, plan, price=100.0):
    import strategy_lab.estimates.metrics  # noqa: F401  (觸發 metric 的 @register)
    from strategy_lab.estimates.model import Estimate
    from strategy_lab.registry import get

    return get("metric", name)().compute(Estimate(plan=plan, market=market(price)))


def text(result):
    return "\n".join(f"{r.label} {r.text}" for r in result.rows)


class TestOrderPlanMetricForBand:
    def test_lists_both_orders_and_the_rule(self):
        out = text(metric("order_plan", band_plan()))
        assert "買 99" in out and "賣 102" in out
        assert "對面" in out and "±2" in out
        assert "進場買" not in out  # 不是單邊的進場 → 平倉

    def test_warns_when_price_is_outside_the_band(self):
        result = metric("order_plan", band_plan(price=103.0), price=103.0)
        assert any(r.warning and "吃單" in r.text for r in result.rows)


class TestRiskForBand:
    def test_says_the_position_can_be_long_or_short(self):
        out = text(metric("risk", band_plan()))
        assert "多單或空單" in out
        assert "無停損" in out


class TestCyclePnlForBand:
    def test_one_crossing_earns_the_band_minus_maker_fees(self):
        result = metric("cycle_pnl", band_plan(buy_pct=1.0, sell_pct=2.0, qty=2.0))
        level1 = result.rows[0]
        assert level1.details["gross"] == pytest.approx((102.0 - 99.0) * 2.0)
        assert "每次穿過區間" in level1.text
