"""Step 1(Broker Protocol 泛化)+ Step 2(LiveBroker)合起來的最終驗證:
把 LiveBroker 接進真正的 StrategyRunner,對照
tests/integration/test_runner_integration.py 的 PaperBroker 版本
——同一套策略邏輯、同一個 runner.py,只換掉 broker,driving 出跟
PaperBroker 版本語意一致的完整進出場循環。runner.py 完全不知道底下是
模擬還是真實(假)交易所。

跟 PaperBroker 版本的關鍵時序差異:PaperBroker.tick(price) 餵價格就會
自動成交;LiveBroker.tick() 是 no-op,成交要靠外部呼叫
server.fill_order() 模擬交易所端撮合,再讓 runner 下一次 tick() 去輪詢
才會發現——這才是真實交易所非同步撮合的樣子。"""

from datetime import datetime, timedelta, timezone

from _stateful_fake_bybit import StatefulFakeBybitServer

from strategy_lab.engine.runner import RunState, StrategyRunner
from strategy_lab.live.broker import LiveBroker
from strategy_lab.live.bybit_client import BybitClient
from strategy_lab.plugins.entry.deviation_from_reference import DeviationFromReferenceEntry
from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow


def make_runner(server: StatefulFakeBybitServer) -> StrategyRunner:
    bybit_client = BybitClient(api_key="test", api_secret="test", http_client=server)
    live_broker = LiveBroker(client=bybit_client, symbol="BTCUSDT")
    return StrategyRunner(
        entry=DeviationFromReferenceEntry(deviation_pct=1.0),
        exit=ReturnToReferenceExit(),
        time_window=WeeklyWindow(end_weekday=5, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=0.01,
        broker=live_broker,
    )


class TestStrategyRunnerWithLiveBroker:
    def test_full_entry_exit_cycle_via_fake_exchange(self):
        server = StatefulFakeBybitServer()
        runner = make_runner(server)

        now = datetime(2026, 8, 2, 4, 0, tzinfo=timezone.utc)  # 星期日,window_end 遠在之後
        runner.start(now, price=1000.0)  # origin_price=1000,進場目標=990

        runner.tick(now, 1000.0)
        assert runner.state == RunState.IDLE

        runner.tick(now + timedelta(minutes=5), 989.0)  # 跌破 990 -> 下限價買單
        assert runner.state == RunState.ENTRY_PENDING
        assert runner.entry_order.price == 990.0

        # LiveBroker.tick() 是 no-op,不會自動成交——要外部模擬交易所撮合
        server.fill_order(runner.entry_order.id, filled_qty=0.01)
        runner.tick(now + timedelta(minutes=10), 989.0)  # 下一次 tick 才會輪詢到已成交
        assert runner.state == RunState.IN_POSITION
        assert runner.active_entry_price == 990.0

        runner.tick(now + timedelta(minutes=15), 995.0)  # 還沒回到 origin=1000
        assert runner.state == RunState.IN_POSITION

        runner.tick(now + timedelta(minutes=20), 1000.0)  # 回到 origin -> 下限價賣單
        assert runner.state == RunState.EXIT_PENDING
        assert runner.exit_order.price == 1000.0

        server.fill_order(runner.exit_order.id, filled_qty=0.01)
        runner.tick(now + timedelta(minutes=25), 1000.0)
        assert runner.state == RunState.IDLE
        assert len(runner.trades) == 1
        assert runner.trades[0].entry_price == 990.0
        assert runner.trades[0].exit_price == 1000.0

        cleanup_time = runner.window_end - timedelta(minutes=1)
        runner.tick(cleanup_time, 1000.0)
        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0.0

    def test_kill_switch_forces_cleanup_via_live_broker_market_close(self):
        """呼應 tests/integration/test_runner_integration.py 的
        TestKillSwitchInterruptsMidPosition——確認 kill_switch 接
        LiveBroker 時,cleanup 呼叫的是 LiveBroker.market_close(),
        會真的透過 BybitClient 送出市價單(這裡是送去假伺服器)。"""
        from strategy_lab.plugins.kill_switch.sustained_breakout import SustainedBreakoutKillSwitch

        server = StatefulFakeBybitServer()
        bybit_client = BybitClient(api_key="test", api_secret="test", http_client=server)
        live_broker = LiveBroker(client=bybit_client, symbol="BTCUSDT")
        runner = StrategyRunner(
            entry=DeviationFromReferenceEntry(deviation_pct=1.0),
            exit=ReturnToReferenceExit(),
            time_window=WeeklyWindow(end_weekday=5, end_time="06:00", cleanup_buffer_minutes=5),
            order_qty=0.01,
            broker=live_broker,
            kill_switch=SustainedBreakoutKillSwitch(
                threshold_price=985.0, reference_price=900.0, days=3, margin_pct=5.0
            ),
        )
        now = datetime(2026, 8, 2, 4, 0, tzinfo=timezone.utc)
        runner.start(now, price=1000.0)

        runner.tick(now, 1000.0)
        runner.tick(now + timedelta(minutes=5), 989.0)
        assert runner.state == RunState.ENTRY_PENDING
        server.fill_order(runner.entry_order.id, filled_qty=0.01)
        runner.tick(now + timedelta(minutes=10), 989.0)
        assert runner.state == RunState.IN_POSITION
        assert runner.broker.position_qty() == 0.01

        # 跨 3 天,價格持平在 989(高於 kill switch 門檻 985,低於出場
        # 目標 1000),讓 kill switch 有機會結算滿 3 天。
        runner.tick(now + timedelta(days=1), 989.0)
        runner.tick(now + timedelta(days=2), 989.0)
        runner.tick(now + timedelta(days=3), 989.0)  # 結算滿 3 天,kill switch 觸發

        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0.0  # LiveBroker.market_close() 已透過假伺服器平倉
