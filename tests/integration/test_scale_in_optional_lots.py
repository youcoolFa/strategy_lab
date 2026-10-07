"""分注可以只有一注或兩注(2026-10-07):entry_prices 第二、三注填 0 = 沒有這一注;有第二注才有第三注。
PaperBroker,不碰網路。"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.broker.paper_broker import PaperBroker
from strategy_lab.engine.runner import RunState
from strategy_lab.engine.scale_in_runner import ScaleInRunner, validate_entry_prices
from strategy_lab.plugins.entry.scale_in import ScaleInEntry
from strategy_lab.plugins.exit.scale_out import ScaleOutExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

NOW = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)


def at(m):
    return NOW + timedelta(minutes=m)


def make(prices, records=None, loop=2):
    runner = ScaleInRunner(
        entry=ScaleInEntry(weights=[2, 3, 1]), exit=ScaleOutExit(distance={"value": 1.0, "unit": "pct"}),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=2.0, entry_prices=prices, loop=loop, broker=PaperBroker(),
        on_order=(records.append if records is not None else None),
    )
    runner.start(NOW, price=1005.0)
    return runner


def open_orders(records, purpose):
    state = {}
    for r in records:
        state[r.order_id] = r
    return sorted([r for r in state.values() if r.purpose == purpose and r.status == "open"], key=lambda r: r.lot)


class TestValidation:
    @pytest.mark.parametrize("prices", [[1000, 990, 980], [1000, 990, 0], [1000, 0, 0]])
    def test_allowed(self, prices):
        assert validate_entry_prices(prices, 3) == [float(p) for p in prices]

    def test_third_lot_needs_second(self):
        with pytest.raises(ValueError, match="有第二注才有第三注"):
            validate_entry_prices([1000, 0, 980], 3)

    def test_first_lot_is_required(self):
        with pytest.raises(ValueError, match="第一注"):
            validate_entry_prices([0, 990, 980], 3)

    @pytest.mark.parametrize("prices", [[1000, -1, 0], [1000, 990]])
    def test_negative_or_wrong_length(self, prices):
        with pytest.raises(ValueError, match="entry_prices"):
            validate_entry_prices(prices, 3)


class TestFewerLots:
    def test_two_lots_only_never_places_a_third(self):
        records = []
        runner = make([1000.0, 990.0, 0.0], records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)  # 第一注成交 → 掛第二注
        runner.tick(at(2), 989.0)  # 第二注成交 → 沒有第三注
        assert open_orders(records, "entry") == []
        assert {r.lot for r in records if r.purpose == "entry"} == {1, 2}
        runner.tick(at(3), 1011.0)  # 兩注都平倉 → 一個 loop
        assert len(runner.events) == 1 and runner.events[0].fills == 4

    def test_single_lot_behaves_like_one_order(self):
        records = []
        runner = make([1000.0, 0.0, 0.0], records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)
        assert open_orders(records, "entry") == []  # 第一注成交後沒有下一注
        runner.tick(at(2), 1011.0)
        assert len(runner.events) == 1
        assert runner.events[0].realized_pnl == pytest.approx((1010 - 1000) * 2.0)

    def test_runner_rejects_third_without_second(self):
        with pytest.raises(ValueError, match="有第二注才有第三注"):
            make([1000.0, 0.0, 980.0])


class TestChangeLotsWhileRunning:
    def test_set_unplaced_third_lot_to_zero_removes_it(self):
        records = []
        runner = make([1000.0, 990.0, 980.0], records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)  # 第一注成交,第二注掛著
        messages = runner.apply_changes(at(2), 999.0, {"entry_prices": [1000.0, 990.0, 0.0]})
        assert [l.index for l in runner.lots] == [1, 2]
        assert any("第3注" in m and "不再建倉" in m for m in messages)
        runner.tick(at(3), 989.0)  # 第二注成交 → 不掛第三注
        assert open_orders(records, "entry") == []

    def test_set_resting_second_lot_to_zero_cancels_it(self):
        records = []
        runner = make([1000.0, 990.0, 980.0], records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)  # 第二注 @990 掛著
        runner.apply_changes(at(2), 999.0, {"entry_prices": [1000.0, 0.0, 0.0]})
        assert open_orders(records, "entry") == []
        assert any(r.lot == 2 and r.status == "canceled" for r in records)
        assert [l.index for l in runner.lots] == [1]

    def test_adding_a_lot_back_during_the_loop(self):
        records = []
        runner = make([1000.0, 990.0, 0.0], records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)  # 第一注成交、第二注掛著
        runner.apply_changes(at(2), 999.0, {"entry_prices": [1000.0, 990.0, 980.0]})
        assert [l.index for l in runner.lots] == [1, 2, 3]
        runner.tick(at(3), 989.0)  # 第二注成交 → 這次會掛第三注
        assert [(r.lot, r.price) for r in open_orders(records, "entry")] == [(3, 980.0)]

    def test_filled_lot_cannot_be_set_to_zero(self):
        runner = make([1000.0, 990.0, 980.0])
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)
        runner.tick(at(2), 989.0)  # 第一、二注都成交
        with pytest.raises(ValueError, match="已成交"):
            runner.apply_changes(at(3), 989.0, {"entry_prices": [1000.0, 0.0, 0.0]})

    def test_next_loop_uses_the_new_lot_count(self):
        records = []
        runner = make([1000.0, 990.0, 980.0], records)
        runner.tick(at(0), 1005.0)
        runner.apply_changes(at(1), 1005.0, {"entry_prices": [1000.0, 0.0, 0.0]})
        runner.tick(at(2), 999.0)
        runner.tick(at(3), 1011.0)  # loop 1 完成
        runner.tick(at(4), 1011.0)  # loop 2 開始
        assert [l.index for l in runner.lots] == [1]
        assert runner.state == RunState.ENTRY_PENDING


class TestAdoptWithFewerLots:
    def test_two_held_lots_with_third_disabled(self):
        from strategy_lab.live.adopt import plan_adoption

        exit_plugin = ScaleOutExit(distance={"value": 0.2, "unit": "pct"})
        orders = [{"orderId": "x1", "side": "Sell", "price": "1.2322", "qty": "20", "reduceOnly": True},
                  {"orderId": "x2", "side": "Sell", "price": "1.2316", "qty": "30", "reduceOnly": True}]
        p = plan_adoption([1.2297, 1.2291, 0.0], [20.0, 30.0, 10.0], exit_plugin, "long", orders, 50.0, 1.2254)
        assert [h.lot for h in p.held] == [1, 2]


class TestEstimatesAndPreflight:
    def test_plan_has_only_active_levels(self):
        from strategy_lab.estimates.metrics import run_metrics
        from strategy_lab.estimates.model import Estimate, MarketSnapshot
        from strategy_lab.estimates.plan import build_scale_in_plan

        market = MarketSnapshot(symbol="X", price=1005.0, equity=500.0, maker_fee_rate=0.0002, taker_fee_rate=0.00055,
                                leverage=10.0, margin_mode="REGULAR_MARGIN", now=NOW)
        plan = build_scale_in_plan(strategy_name="s", entry=ScaleInEntry(weights=[2, 3, 1]),
                                   exit=ScaleOutExit(distance={"value": 1.0, "unit": "pct"}), direction="long",
                                   entry_prices=[1000.0, 990.0, 0.0], qtys=[2.0, 3.0, 1.0], market=market,
                                   cleanup_at=NOW + timedelta(days=1))
        assert [l.index for l in plan.levels] == [1, 2]
        rows = {r.key: r for res in run_metrics(Estimate(plan=plan, market=market)) for r in res.rows}
        assert rows["level_2"].value is not None and rows["level_3"].value is None
