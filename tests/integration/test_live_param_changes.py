"""策略運作中改參數(建倉價、平倉距離、loop):runner.apply_changes()。PaperBroker,不碰網路。

規則:
- 建倉價:還沒成交的注取消重掛(還沒輪到的之後用新價);已成交的注不動;新價越過現價 → 拒絕
- 平倉距離:已持有的注取消重掛平倉單;之後成交的注用新距離
- loop:新的總次數必須大於已完成的 loop 數
- 取消前一刻剛好成交 → 不重掛(交給正常成交流程),避免重複下單
"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.broker.paper_broker import PaperBroker
from strategy_lab.engine.scale_in_runner import ScaleInRunner
from strategy_lab.plugins.entry.resting_deviation_from_reference import RestingDeviationFromReferenceEntry
from strategy_lab.plugins.entry.scale_in import ScaleInEntry
from strategy_lab.plugins.exit.resting_return_to_reference import RestingReturnToReferenceExit
from strategy_lab.plugins.exit.scale_out import ScaleOutExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow
from strategy_lab.engine.runner import StrategyRunner

NOW = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)


def at(m):
    return NOW + timedelta(minutes=m)


def make(records=None, loop=2, broker=None):
    runner = ScaleInRunner(
        entry=ScaleInEntry(weights=[2, 3, 1]), exit=ScaleOutExit(distance={"value": 1.0, "unit": "pct"}),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=2.0, entry_prices=[1000.0, 990.0, 980.0], loop=loop,
        broker=broker or PaperBroker(), on_order=(records.append if records is not None else None),
    )
    runner.start(NOW, price=1005.0)
    return runner


def open_orders(records, purpose):
    state = {}
    for r in records:
        state[r.order_id] = r
    return sorted([r for r in state.values() if r.purpose == purpose and r.status == "open"], key=lambda r: r.lot)


class TestEntryPrices:
    def test_open_entry_order_is_replaced_at_the_new_price(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 1005.0)  # 掛第一注 @1000
        messages = runner.apply_changes(at(1), 1005.0, {"entry_prices": [1002.0, 995.0, 985.0]})

        assert [(r.lot, r.price) for r in open_orders(records, "entry")] == [(1, 1002.0)]
        assert any(r.lot == 1 and r.status == "canceled" and r.price == 1000.0 for r in records)
        assert runner.entry_prices == [1002.0, 995.0, 985.0]
        assert any("第1注" in m and "1002" in m for m in messages)

    def test_filled_lot_keeps_its_price_and_later_lots_use_new_prices(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)  # 第一注成交 @1000,掛第二注 @990
        runner.apply_changes(at(2), 999.0, {"entry_prices": [1001.0, 993.0, 984.0]})

        assert runner.lots[0].entry_price == 1000.0  # 已成交,不動
        assert [(r.lot, r.price) for r in open_orders(records, "entry")] == [(2, 993.0)]
        [exit_order] = open_orders(records, "exit")
        assert exit_order.price == pytest.approx(1010.0)  # 平倉照原本建倉價
        runner.tick(at(3), 992.0)  # 第二注 @993 成交 → 掛第三注用新價
        assert [(r.lot, r.price) for r in open_orders(records, "entry")] == [(3, 984.0)]

    def test_price_crossing_the_market_is_rejected_and_nothing_changes(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 1005.0)
        with pytest.raises(ValueError, match="越過現價"):
            runner.apply_changes(at(1), 1005.0, {"entry_prices": [1006.0, 995.0, 985.0]})
        assert runner.entry_prices == [1000.0, 990.0, 980.0]
        assert [(r.lot, r.price) for r in open_orders(records, "entry")] == [(1, 1000.0)]

    def test_wrong_number_of_prices_is_rejected(self):
        runner = make()
        with pytest.raises(ValueError, match="entry_prices"):
            runner.apply_changes(at(1), 1005.0, {"entry_prices": [1000.0, 990.0]})

    def test_order_filled_right_before_cancel_is_not_placed_again(self):
        """取消前一刻剛好成交:不能再掛一張新的(會變成重複建倉)。"""
        records = []
        broker = PaperBroker()
        runner = make(records, broker=broker)
        runner.tick(at(0), 1005.0)
        broker.tick(999.0)  # 交易所上已經成交,runner 還沒輪詢到
        runner.apply_changes(at(1), 999.5, {"entry_prices": [999.0, 995.0, 985.0]})
        entries = [r for r in records if r.purpose == "entry" and r.lot == 1]
        assert len({r.order_id for r in entries}) == 1  # 沒有重掛
        runner.tick(at(2), 999.5)  # 正常流程接手:記成交、掛平倉、掛第二注(新價)
        assert runner.lots[0].filled_price == 1000.0
        assert [(r.lot, r.price) for r in open_orders(records, "entry")] == [(2, 995.0)]


class TestDistance:
    def test_held_lots_get_their_exit_orders_replaced(self):
        records = []
        runner = make(records)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)  # 第一注成交,平倉 @1010
        messages = runner.apply_changes(at(2), 999.0, {"distance": {"value": 0.5, "unit": "pct"}})

        [exit_order] = open_orders(records, "exit")
        assert exit_order.price == pytest.approx(1005.0)  # 1000 × 1.005
        assert any("平倉距離" in m for m in messages)
        runner.tick(at(3), 989.0)  # 第二注成交 → 用新距離
        assert {r.lot: r.price for r in open_orders(records, "exit")}[2] == pytest.approx(990 * 1.005)

    def test_bad_distance_is_rejected(self):
        runner = make()
        with pytest.raises(ValueError, match="distance"):
            runner.apply_changes(at(1), 1005.0, {"distance": {"value": 0, "unit": "pct"}})


class TestLoop:
    def test_loop_can_be_raised_or_set_unlimited(self):
        runner = make(loop=0)
        runner.apply_changes(at(1), 1005.0, {"loop": 3})
        assert runner.loop == 3
        runner.apply_changes(at(1), 1005.0, {"loop": None})
        assert runner.loop is None

    def test_loop_lower_than_completed_loops_is_rejected(self):
        runner = make(loop=2)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)
        runner.tick(at(2), 1011.0)  # 完成第 1 個 loop
        with pytest.raises(ValueError, match="已完成"):
            runner.apply_changes(at(3), 1011.0, {"loop": 0})  # 總共 1 個 = 已完成的數量
        runner.apply_changes(at(3), 1011.0, {"loop": 1})  # 總共 2 個,還能再做 1 個
        assert runner.loop == 1

    def test_non_scale_in_runner_only_accepts_loop(self):
        runner = StrategyRunner(
            entry=RestingDeviationFromReferenceEntry(deviation_pct=1.0), exit=RestingReturnToReferenceExit(),
            time_window=WeeklyWindow(), order_qty=1.0, loop=0,
        )
        runner.start(NOW, 1000.0)
        runner.apply_changes(at(1), 1000.0, {"loop": 2})
        assert runner.loop == 2
        with pytest.raises(ValueError, match="分注"):
            runner.apply_changes(at(1), 1000.0, {"entry_prices": [990.0]})
