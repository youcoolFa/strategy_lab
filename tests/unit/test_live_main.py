"""live/main.py 的邏輯測試——不需要任何真實 API key、不會發真正的網路
請求。`run_forever()` 的無限迴圈本身,用可注入的假時鐘/假 sleep 讓它在
測試裡跑一段有限的次數,不需要真的等待牆上時間經過。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strategy_lab.engine.runner import RunState
from strategy_lab.live.config import ExecutionConfig
from strategy_lab.live.main import build_runner_and_symbol, get_current_price, run_forever, to_bybit_symbol

HKT = ZoneInfo("Asia/Hong_Kong")


class TestToBybitSymbol:
    def test_strips_slash(self):
        assert to_bybit_symbol("BTC/USDT") == "BTCUSDT"

    def test_strips_colon_for_ccxt_style_symbols(self):
        assert to_bybit_symbol("BTC/USDT:USDT") == "BTCUSDT"

    def test_already_bybit_format_is_unchanged(self):
        assert to_bybit_symbol("BTCUSDT") == "BTCUSDT"


class TestBuildRunnerAndSymbol:
    def test_wires_weekend_strategy_correctly(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/weekend_mean_reversion.yaml", dry_run=True, testnet=True)

        runner, symbol = build_runner_and_symbol(config)

        assert symbol == "BTCUSDT"
        assert runner.broker.symbol == "BTCUSDT"
        assert runner.broker.dry_run is True
        assert runner.order_qty == 1.0

    def test_symbol_override_takes_precedence_over_yaml(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(
            strategy_path="strategies/weekend_mean_reversion.yaml", symbol_override="ETHUSDT", dry_run=True
        )

        _, symbol = build_runner_and_symbol(config)

        assert symbol == "ETHUSDT"

    def test_dry_run_false_still_builds_without_real_network_call(self, monkeypatch):
        """建構 BybitClient(即使 dry_run=False)本身不應該發網路請求
        ——只是準備好連線設定,實際下單才會真的呼叫出去。"""
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/weekend_mean_reversion.yaml", dry_run=False, testnet=True)

        runner, symbol = build_runner_and_symbol(config)

        assert runner.broker.dry_run is False


class FakeLiveBroker:
    def __init__(self, last_price):
        self.symbol = "BTCUSDT"
        self.dry_run = True
        self._last_price = last_price
        self.client = self

    def get_last_price(self, symbol):
        return self._last_price


class FakeRunner:
    def __init__(self, broker):
        self.broker = broker


class TestGetCurrentPrice:
    def test_uses_rest_fallback_when_live_feed_disabled(self):
        runner = FakeRunner(FakeLiveBroker(last_price=60000.0))
        config = ExecutionConfig(use_live_ticker_feed=False)

        assert get_current_price(runner, config, "BTCUSDT") == 60000.0

    def test_uses_ticker_feed_price_when_available(self, monkeypatch):
        import strategy_lab.live.main as main_module

        monkeypatch.setattr(main_module.ticker_feed, "get_last_price", lambda: 61234.5)
        runner = FakeRunner(FakeLiveBroker(last_price=99999.0))  # 不應該被用到
        config = ExecutionConfig(use_live_ticker_feed=True)

        assert get_current_price(runner, config, "BTCUSDT") == 61234.5

    def test_falls_back_to_rest_when_ticker_feed_has_no_data_yet(self, monkeypatch):
        import strategy_lab.live.main as main_module

        monkeypatch.setattr(main_module.ticker_feed, "get_last_price", lambda: None)
        runner = FakeRunner(FakeLiveBroker(last_price=60000.0))
        config = ExecutionConfig(use_live_ticker_feed=True)

        assert get_current_price(runner, config, "BTCUSDT") == 60000.0


class TestRunForever:
    def test_stops_via_request_stop_and_drives_full_cycle(self, monkeypatch):
        """用可注入的假時鐘/假 sleep,讓迴圈跑一段確定的次數就結束,不用
        真的等待牆上時間經過——dry_run=True 全程不碰真實網路。"""
        import strategy_lab.live.main as main_module

        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/weekend_mean_reversion.yaml", dry_run=True, poll_interval_seconds=0)
        runner, symbol = build_runner_and_symbol(config)

        prices = iter([1000.0, 989.0, 989.0, 1000.0, 1000.0])
        clock = {"now": datetime(2026, 8, 1, 4, 0, tzinfo=HKT)}

        def fake_now():
            return clock["now"]

        def fake_get_price(runner, config, symbol):
            clock["now"] += timedelta(minutes=5)
            try:
                return next(prices)
            except StopIteration:
                runner.request_stop()  # 價格用完了,主動要求停止,避免無限迴圈
                return 1000.0

        sleep_calls = []
        monkeypatch.setattr(main_module.time, "sleep", lambda s: sleep_calls.append(s))
        monkeypatch.setattr(main_module, "get_current_price", fake_get_price)

        run_forever(runner, config, symbol, now_fn=fake_now)

        assert runner.state == RunState.STOPPED
        assert len(runner.trades) == 1
        assert sleep_calls  # 確認真的有經過輪詢間隔這個步驟,不是直接跳過
