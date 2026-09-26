"""sat_strategy 機制(一啟動就掛限價單)的完整流程:第一個 tick 就掛進場單、
成交後馬上掛出場單、出場成交後再補回同價位的進場單、窗口結束清理。
用 PaperBroker + 手動價格序列,不碰網路。"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.engine.runner import RunState, StrategyRunner
from strategy_lab.plugins.entry.resting_deviation_from_reference import RestingDeviationFromReferenceEntry
from strategy_lab.plugins.exit.resting_return_to_reference import RestingReturnToReferenceExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

NOW = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)


def make_runner(direction="long", order_type="limit"):
    return StrategyRunner(
        entry=RestingDeviationFromReferenceEntry(deviation_pct=1.0),
        exit=RestingReturnToReferenceExit(),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=1.0,
        order_type=order_type,
        direction=direction,
    )


def at(minutes):
    return NOW + timedelta(minutes=minutes)


class TestLongRestingCycle:
    def test_entry_order_is_placed_on_first_tick_without_waiting_for_price(self):
        runner = make_runner()
        runner.start(NOW, price=1000.0)

        runner.tick(NOW, 1000.0)  # 價格還在 origin,sat_strategy 一啟動就掛單

        assert runner.state == RunState.ENTRY_PENDING
        assert runner.entry_order.price == 990.0
        assert runner.entry_order.side == "buy"

    def test_entry_stays_pending_until_price_reaches_limit(self):
        runner = make_runner()
        runner.start(NOW, price=1000.0)
        runner.tick(at(0), 1000.0)

        runner.tick(at(5), 995.0)

        assert runner.state == RunState.ENTRY_PENDING

    def test_full_round_trip_then_entry_is_re_placed_at_same_price(self):
        runner = make_runner()
        runner.start(NOW, price=1000.0)

        runner.tick(at(0), 1000.0)  # 掛買 990
        runner.tick(at(5), 989.0)  # 碰到 990 成交
        assert runner.state == RunState.IN_POSITION
        assert runner.active_entry_price == 990.0

        runner.tick(at(10), 989.0)  # 成交後馬上掛平倉 1000,不等價格回來
        assert runner.state == RunState.EXIT_PENDING
        assert runner.exit_order.price == 1000.0
        assert runner.exit_order.reduce_only is True

        runner.tick(at(15), 1000.0)  # 平倉成交
        assert runner.state == RunState.IDLE
        assert len(runner.trades) == 1
        assert runner.trades[0].pnl == pytest.approx(10.0)

        runner.tick(at(20), 1000.0)  # 補回同價位的進場單
        assert runner.state == RunState.ENTRY_PENDING
        assert runner.entry_order.price == 990.0

    def test_canceled_entry_order_is_re_placed(self):
        runner = make_runner()
        runner.start(NOW, price=1000.0)
        runner.tick(at(0), 1000.0)
        first = runner.entry_order

        runner.broker.cancel_order(first.id)  # 模擬交易所取消/過期
        runner.tick(at(5), 1000.0)  # 偵測到取消 -> 回到 IDLE
        runner.tick(at(10), 1000.0)  # 重掛

        assert runner.state == RunState.ENTRY_PENDING
        assert runner.entry_order.id != first.id
        assert runner.entry_order.price == 990.0

    def test_cleanup_cancels_resting_order_and_leaves_no_position(self):
        runner = make_runner()
        runner.start(NOW, price=1000.0)
        runner.tick(at(0), 1000.0)
        runner.tick(at(5), 989.0)  # 買進成交
        runner.tick(at(10), 989.0)  # 掛平倉

        runner.tick(runner.window_end - timedelta(minutes=1), 989.0)

        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0.0


class TestShortRestingCycle:
    def test_short_entry_is_sell_above_origin_and_exit_is_buy_back_at_origin(self):
        runner = make_runner(direction="short")
        runner.start(NOW, price=1000.0)

        runner.tick(at(0), 1000.0)
        assert runner.state == RunState.ENTRY_PENDING
        assert runner.entry_order.price == 1010.0
        assert runner.entry_order.side == "sell"

        runner.tick(at(5), 1011.0)  # 漲到 1010 成交,開空
        assert runner.state == RunState.IN_POSITION
        assert runner.broker.position_qty() == -1.0

        runner.tick(at(10), 1011.0)  # 馬上掛買回 1000
        assert runner.state == RunState.EXIT_PENDING
        assert runner.exit_order.price == 1000.0
        assert runner.exit_order.side == "buy"
        assert runner.exit_order.reduce_only is True

        runner.tick(at(15), 1000.0)
        assert runner.state == RunState.IDLE
        assert runner.trades[0].pnl == pytest.approx(10.0)
        assert runner.broker.position_qty() == 0.0


class TestRestingPluginsRequireLimitOrders:
    def test_market_order_type_is_rejected(self):
        # 一啟動就掛單的機制下,rule 永遠成立;用市價單等於一啟動就市價
        # 買進、一成交就市價平倉,完全不是這個策略的意思。
        with pytest.raises(ValueError, match="limit"):
            make_runner(order_type="market")
