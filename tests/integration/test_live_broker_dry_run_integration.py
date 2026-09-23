"""Step 3(dry_run 安全開關)的最終驗證:把 dry_run=True 的 LiveBroker
接進真正的 StrategyRunner,跑一次完整進出場循環——這是最強的安全證明:
不是「假裝檢查過某個旗標」,是整條路徑跑完之後,直接斷言底層的假
BybitClient(換成真的 pybit client 也一樣)**完全沒有被呼叫過**。"""

from datetime import datetime, timedelta, timezone

from strategy_lab.engine.runner import RunState, StrategyRunner
from strategy_lab.live.broker import LiveBroker
from strategy_lab.plugins.entry.deviation_from_reference import DeviationFromReferenceEntry
from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow


class RecordingBybitClient:
    """故意不是真的 BybitClient,也不接受任何 http_client——如果
    dry_run 有任何漏洞讓呼叫真的滲透到這裡,馬上就看得到,而不是悄悄打
    到假伺服器、測試看起來「過了」但其實驗證的東西是錯的。"""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            raise AssertionError(f"dry_run=True 時不應該呼叫 BybitClient.{name}()")

        return record


class TestFullCycleNeverTouchesRealClient:
    def test_entry_exit_cleanup_cycle_makes_zero_real_api_calls(self):
        client = RecordingBybitClient()
        live_broker = LiveBroker(client=client, symbol="BTCUSDT", dry_run=True)
        runner = StrategyRunner(
            entry=DeviationFromReferenceEntry(deviation_pct=1.0),
            exit=ReturnToReferenceExit(),
            time_window=WeeklyWindow(end_weekday=5, end_time="06:00", cleanup_buffer_minutes=5),
            order_qty=0.01,
            broker=live_broker,
        )

        now = datetime(2026, 8, 2, 4, 0, tzinfo=timezone.utc)
        runner.start(now, price=1000.0)

        runner.tick(now, 1000.0)
        assert runner.state == RunState.IDLE

        runner.tick(now + timedelta(minutes=5), 989.0)  # 跌破 990 -> 下單
        assert runner.state == RunState.ENTRY_PENDING

        runner.tick(now + timedelta(minutes=10), 989.0)  # dry-run 訂單第一次被查詢就視為成交
        assert runner.state == RunState.IN_POSITION
        assert runner.active_entry_price == 990.0

        runner.tick(now + timedelta(minutes=15), 1000.0)  # 回到 origin -> 下出場單
        assert runner.state == RunState.EXIT_PENDING

        runner.tick(now + timedelta(minutes=20), 1000.0)
        assert runner.state == RunState.IDLE
        assert len(runner.trades) == 1
        assert runner.trades[0].entry_price == 990.0
        assert runner.trades[0].exit_price == 1000.0

        cleanup_time = runner.window_end - timedelta(minutes=1)
        runner.tick(cleanup_time, 1000.0)
        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0.0

        assert client.calls == []  # 整條路徑跑完,底層 client 完全沒被碰過
