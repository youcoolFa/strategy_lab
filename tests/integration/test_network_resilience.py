"""網路錯誤韌性(對齊 sat_strategy「不放棄」,docs/ARCHITECTURE.md §6.23):

某一步網路失敗、這一輪 tick 中斷後,下一輪 tick 重做要安全——
- 不重複掛已經掛上的單
- 沒掛上的單會補掛
- 收尾做到一半中斷,下一輪繼續收尾到 STOPPED
run_forever() 本身遇到網路錯誤不當掉,下一輪再試。
"""

from datetime import datetime, timedelta, timezone

import pytest
import requests

from strategy_lab.broker.paper_broker import PaperBroker
from strategy_lab.engine.runner import RunState
from strategy_lab.engine.scale_in_runner import ScaleInRunner
from strategy_lab.plugins.entry.scale_in import ScaleInEntry
from strategy_lab.plugins.exit.scale_out import ScaleOutExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

NOW = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)


def at(m):
    return NOW + timedelta(minutes=m)


class FlakyBroker(PaperBroker):
    """指定的方法在第 n 次呼叫時丟一次網路錯誤(呼叫本身不生效),其餘照 PaperBroker。"""

    def __init__(self):
        super().__init__()
        self.fail_on = {}  # 方法名 -> 第幾次呼叫要失敗(1 起算)
        self.counts = {}

    def _maybe_fail(self, name):
        self.counts[name] = self.counts.get(name, 0) + 1
        if self.fail_on.get(name) == self.counts[name]:
            raise requests.exceptions.ConnectionError(f"{name} 斷線")

    def place_limit_buy(self, price, qty):
        self._maybe_fail("place_limit_buy")
        return super().place_limit_buy(price, qty)

    def place_limit_sell(self, price, qty):
        self._maybe_fail("place_limit_sell")
        return super().place_limit_sell(price, qty)

    def position_qty(self):
        self._maybe_fail("position_qty")
        return super().position_qty()


def make_scale_in(broker):
    runner = ScaleInRunner(
        entry=ScaleInEntry(weights=[2, 3, 1]),
        exit=ScaleOutExit(distance={"value": 1.0, "unit": "pct"}),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=2.0,
        entry_prices=[1000.0, 990.0, 980.0],
        broker=broker,
    )
    runner.start(NOW, price=1005.0)
    return runner


def open_buy_orders(broker):
    return sorted(o.price for o in broker._orders.values() if o.status == "open" and o.side == "buy")


class TestScaleInRecoversFromNetworkFailure:
    def test_next_lot_placement_failure_does_not_duplicate_orders(self):
        """依序掛單:第一注成交後掛平倉單、再掛第二注;第二注下單斷線 → 下一輪只補掛第二注,
        第一注的建倉單、平倉單都不會重複。"""
        broker = FlakyBroker()
        broker.fail_on = {"place_limit_buy": 2}  # 第 2 次買單 = 第二注
        runner = make_scale_in(broker)
        runner.tick(at(0), 1005.0)  # 只掛第一注

        with pytest.raises(requests.exceptions.ConnectionError):
            runner.tick(at(1), 999.0)  # 第一注成交 → 掛平倉 → 掛第二注時斷線
        runner.tick(at(2), 999.0)  # 下一輪:補掛第二注

        assert open_buy_orders(broker) == [990.0]
        exits = [o for o in broker._orders.values() if o.status == "open" and o.side == "sell"]
        assert [(o.price, o.qty) for o in exits] == [(pytest.approx(1010.0), 2.0)]
        assert runner.state == RunState.IN_POSITION

    def test_exit_placement_failure_after_fill_is_retried_next_tick(self):
        broker = FlakyBroker()
        broker.fail_on = {"place_limit_sell": 1}  # 第一注成交後掛平倉單斷線
        runner = make_scale_in(broker)
        runner.tick(at(0), 1005.0)

        with pytest.raises(requests.exceptions.ConnectionError):
            runner.tick(at(1), 999.0)  # 第一注成交
        runner.tick(at(2), 999.0)

        exits = [o for o in broker._orders.values() if o.status == "open" and o.side == "sell"]
        assert [(o.price, o.qty) for o in exits] == [(pytest.approx(1010.0), 2.0)]
        assert runner.state == RunState.IN_POSITION

    def test_loop_still_ends_when_tick_is_interrupted_after_event_completes(self):
        broker = FlakyBroker()
        runner = make_scale_in(broker)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)  # 第一注成交、掛平倉
        runner.tick(at(2), 1011.0)  # 平倉成交 → event 完成 → loop 結束
        assert len(runner.events) == 1
        assert runner.state == RunState.IDLE
        runner.tick(at(3), 1011.0)  # 下一個 loop 從第一注重新開始(依序掛單)
        assert open_buy_orders(broker) == [1000.0]


class TestCleanupResumesAfterNetworkFailure:
    def test_interrupted_cleanup_is_redone_next_tick(self):
        broker = FlakyBroker()
        runner = make_scale_in(broker)
        runner.tick(at(0), 1005.0)
        runner.tick(at(1), 999.0)  # 第一注持倉
        runner.request_stop()
        broker.fail_on = {"position_qty": broker.counts.get("position_qty", 0) + 1}

        with pytest.raises(requests.exceptions.ConnectionError):
            runner.tick(at(2), 999.0)  # 收尾:取消完掛單、查部位時斷線
        assert runner.state != RunState.STOPPED
        assert runner.stop_reason == "stop_requested"

        runner.tick(at(3), 999.0)
        assert runner.state == RunState.STOPPED
        assert broker.position_qty() == 0


class TestRunForeverToleratesNetworkErrors:
    def test_price_and_tick_failures_do_not_crash_the_loop(self, monkeypatch):
        import strategy_lab.live.main as main_module
        from strategy_lab.live.config import ExecutionConfig
        from strategy_lab.live.main import build_runner_and_symbol, run_forever

        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/weekend_mean_reversion.yaml", dry_run=True, poll_interval_seconds=0)
        runner, symbol = build_runner_and_symbol(config)

        HKT = main_module.HKT
        clock = {"now": datetime(2026, 8, 1, 4, 0, tzinfo=HKT), "n": 0}
        prices = [1000.0, None, 989.0, 989.0, 1000.0, 1000.0]  # None = 查價斷線

        def fake_get_price(runner, config, symbol):
            clock["now"] += timedelta(minutes=5)
            i = clock["n"]
            clock["n"] += 1
            if i >= len(prices):
                runner.request_stop()
                return 1000.0
            if prices[i] is None:
                raise requests.exceptions.ConnectionError("查價斷線")
            return prices[i]

        real_tick = runner.tick
        tick_calls = {"n": 0}

        def flaky_tick(now, price):
            tick_calls["n"] += 1
            if tick_calls["n"] == 3:
                raise requests.exceptions.ReadTimeout("tick 逾時")
            return real_tick(now, price)

        monkeypatch.setattr(runner, "tick", flaky_tick)
        monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
        monkeypatch.setattr(main_module, "get_current_price", fake_get_price)

        run_forever(runner, config, symbol, now_fn=lambda: clock["now"])

        assert runner.state == RunState.STOPPED
        assert len(runner.trades) == 1
