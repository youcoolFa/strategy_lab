"""live/main.py 的邏輯測試——不需要任何真實 API key、不會發真正的網路
請求。`run_forever()` 的無限迴圈本身,用可注入的假時鐘/假 sleep 讓它在
測試裡跑一段有限的次數,不需要真的等待牆上時間經過。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from strategy_lab.dsl.order_config import PositionSizing
from strategy_lab.engine.runner import RunState
from strategy_lab.live.bybit_client import BybitClient
from strategy_lab.live.config import ExecutionConfig
from strategy_lab.live.main import (
    LeftoverExchangeStateError,
    build_runner_and_symbol,
    ensure_clean_start,
    main,
    get_current_price,
    resolve_origin_price,
    run_forever,
    to_bybit_symbol,
)

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
        assert runner.order_qty == 1.0  # 預設 position_sizing 是 fixed_qty, value=1.0
        assert runner.order_type == "limit"  # 預設值,對齊 ExecutionConfig.order_type

    def test_order_type_market_is_threaded_through_to_runner(self, monkeypatch):
        # weekend_mean_reversion 是一啟動就掛單的機制,只接受 limit;
        # 這裡用 ma_crossover_bracket 驗證 market 有被傳進 runner。
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/ma_crossover_bracket.yaml", order_type="market")

        runner, _ = build_runner_and_symbol(config)

        assert runner.order_type == "market"

    def test_weekend_strategy_with_market_order_type_is_rejected(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/weekend_mean_reversion.yaml", order_type="market")

        with pytest.raises(ValueError, match="limit"):
            build_runner_and_symbol(config)

    def test_symbol_override_takes_precedence_over_yaml(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(
            strategy_path="strategies/weekend_mean_reversion.yaml", symbol_override="ETHUSDT", dry_run=True
        )

        _, symbol = build_runner_and_symbol(config)

        assert symbol == "ETHUSDT"

    def test_category_is_threaded_through_to_bybit_client(self, monkeypatch):
        """category(spot/linear/...)原本寫死在 BybitClient 內部,現在是
        ExecutionConfig 的欄位,要確認 build_runner_and_symbol() 真的把
        它傳給 BybitClient 建構子,不是讀了卻沒用。"""
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        captured = {}
        original_init = BybitClient.__init__

        def capture_init(self, *args, **kwargs):
            captured["category"] = kwargs.get("category")
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(BybitClient, "__init__", capture_init)
        config = ExecutionConfig(strategy_path="strategies/weekend_mean_reversion.yaml", category="spot")

        build_runner_and_symbol(config)

        assert captured["category"] == "spot"

    def test_dry_run_false_still_builds_without_real_network_call(self, monkeypatch):
        """建構 BybitClient(即使 dry_run=False)本身不應該發網路請求
        ——只是準備好連線設定,實際下單才會真的呼叫出去。"""
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/weekend_mean_reversion.yaml", dry_run=False, testnet=True)

        runner, symbol = build_runner_and_symbol(config)

        assert runner.broker.dry_run is False


class TestBuildRunnerAndSymbolPositionSizing:
    def test_fixed_qty_mode_does_not_call_get_last_price(self, monkeypatch):
        """fixed_qty 不需要知道價格,建構 runner 不該多打一次網路請求
        ——跟 test_dry_run_false_still_builds_without_real_network_call
        同一個原則,這裡從 position_sizing 的角度再驗證一次。"""
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")

        def fail_if_called(self, symbol):
            raise AssertionError("fixed_qty 模式不應該呼叫 get_last_price()")

        monkeypatch.setattr(BybitClient, "get_last_price", fail_if_called)
        config = ExecutionConfig(
            strategy_path="strategies/weekend_mean_reversion.yaml",
            position_sizing=PositionSizing(mode="fixed_qty", value=0.02),
        )

        runner, _ = build_runner_and_symbol(config)

        assert runner.order_qty == 0.02

    def test_fixed_quote_amount_mode_uses_current_price(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        monkeypatch.setattr(BybitClient, "get_last_price", lambda self, symbol: 50000.0)
        config = ExecutionConfig(
            strategy_path="strategies/weekend_mean_reversion.yaml",
            position_sizing=PositionSizing(mode="fixed_quote_amount", value=500.0),
        )

        runner, _ = build_runner_and_symbol(config)

        assert runner.order_qty == pytest.approx(0.01)  # 500 / 50000

    def test_account_percentage_without_account_value_queries_real_equity(self, monkeypatch):
        """沒有手動覆蓋 account_value 時,自動呼叫
        BybitClient.get_account_equity() 查真實帳戶權益——不再是
        「沒給就 raise」,見 dsl/order_config.py 的 _resolve_order_qty()
        wiring。"""
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        monkeypatch.setattr(BybitClient, "get_last_price", lambda self, symbol: 50000.0)
        monkeypatch.setattr(BybitClient, "get_account_equity", lambda self: 10000.0)
        config = ExecutionConfig(
            strategy_path="strategies/weekend_mean_reversion.yaml",
            position_sizing=PositionSizing(mode="account_percentage", value=2.0),
        )

        runner, _ = build_runner_and_symbol(config)

        # 10000 的 2% = 200,除以現價 50000 = 0.004
        assert runner.order_qty == pytest.approx(0.004)

    def test_account_percentage_with_explicit_account_value_does_not_query_real_equity(self, monkeypatch):
        """使用者自己在設定檔填了 account_value,就用那個值,不去查真實
        權益——這是刻意留給使用者「用比真實權益更保守的假設值」的覆蓋
        管道。"""
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        monkeypatch.setattr(BybitClient, "get_last_price", lambda self, symbol: 50000.0)

        def fail_if_called(self):
            raise AssertionError("account_value 已明確給定,不該再查真實權益")

        monkeypatch.setattr(BybitClient, "get_account_equity", fail_if_called)
        config = ExecutionConfig(
            strategy_path="strategies/weekend_mean_reversion.yaml",
            position_sizing=PositionSizing(mode="account_percentage", value=2.0),
            account_value=5000.0,
        )

        runner, _ = build_runner_and_symbol(config)

        # 5000 的 2% = 100,除以現價 50000 = 0.002
        assert runner.order_qty == pytest.approx(0.002)


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


class TestResolveOriginPrice:
    def test_uses_manual_origin_price_when_set(self):
        config = ExecutionConfig(origin_price=0.4772)
        assert resolve_origin_price(config, current_price=0.49) == 0.4772

    def test_falls_back_to_current_price_when_not_set(self):
        config = ExecutionConfig(origin_price=None)
        assert resolve_origin_price(config, current_price=0.49) == 0.49

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_rejects_non_positive_manual_origin_price(self, bad):
        config = ExecutionConfig(origin_price=bad)
        with pytest.raises(ValueError, match="origin_price"):
            resolve_origin_price(config, current_price=0.49)


class TestRunForeverUsesManualOriginPrice:
    def test_runner_origin_is_the_manual_value_not_the_live_price(self, monkeypatch):
        import strategy_lab.live.main as main_module

        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(
            strategy_path="strategies/weekend_mean_reversion.yaml",
            dry_run=True,
            poll_interval_seconds=0,
            origin_price=1010.0,
        )
        runner, symbol = build_runner_and_symbol(config)
        clock = {"now": datetime(2026, 8, 1, 4, 0, tzinfo=HKT)}

        def fake_get_price(runner, config, symbol):
            runner.request_stop()
            return 1000.0

        monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
        monkeypatch.setattr(main_module, "get_current_price", fake_get_price)

        run_forever(runner, config, symbol, now_fn=lambda: clock["now"])

        assert runner.origin_price == 1010.0


class FakeExchangeClient:
    def __init__(self, open_orders=None, position_qty=0.0):
        self.open_orders = open_orders or []
        self.position = position_qty
        self.calls = []

    def get_open_orders(self, symbol):
        self.calls.append(("get_open_orders", symbol))
        return self.open_orders

    def get_position_qty(self, symbol):
        self.calls.append(("get_position_qty", symbol))
        return self.position


class TestEnsureCleanStart:
    """當機/強制關閉後重啟:舊的掛單還在交易所上,新 process 不知道,會再掛
    一張變成兩倍。啟動前先查,有殘留就拒絕啟動,讓人手動處理。"""

    def test_passes_when_no_orders_and_no_position(self):
        client = FakeExchangeClient()
        ensure_clean_start(client, "WLDUSDT", dry_run=False)
        assert client.calls == [("get_open_orders", "WLDUSDT"), ("get_position_qty", "WLDUSDT")]

    def test_refuses_when_open_orders_exist(self):
        client = FakeExchangeClient(open_orders=[{"orderId": "o1", "side": "Buy", "price": "0.4764", "qty": "41.1"}])
        with pytest.raises(LeftoverExchangeStateError, match="0.4764"):
            ensure_clean_start(client, "WLDUSDT", dry_run=False)

    def test_refuses_when_position_exists(self):
        client = FakeExchangeClient(position_qty=-41.1)
        with pytest.raises(LeftoverExchangeStateError, match="-41.1"):
            ensure_clean_start(client, "WLDUSDT", dry_run=False)

    def test_skipped_in_dry_run(self):
        # dry-run 不會碰真實訂單,真實帳戶上有什麼都跟模擬無關。
        client = FakeExchangeClient(open_orders=[{"orderId": "o1"}], position_qty=5.0)
        ensure_clean_start(client, "WLDUSDT", dry_run=True)
        assert client.calls == []


class TestRunForeverRefusesDirtyStart:
    def test_raises_before_start_and_places_nothing(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/weekend_mean_reversion.yaml", dry_run=False, testnet=True)
        runner, symbol = build_runner_and_symbol(config)
        leftover = FakeExchangeClient(open_orders=[{"orderId": "old", "side": "Buy", "price": "992.5", "qty": "1"}])
        runner.broker.client = leftover

        with pytest.raises(LeftoverExchangeStateError):
            run_forever(runner, config, symbol, now_fn=lambda: datetime(2026, 8, 1, 4, 0, tzinfo=HKT))

        assert runner.origin_price is None  # 還沒 start,沒有下任何單


class TestMainConfigArgument:
    def _capture(self, monkeypatch):
        import strategy_lab.live.main as main_module

        captured = {}
        monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
        monkeypatch.setattr(main_module, "build_runner_and_symbol", lambda config: captured.setdefault("config", config) and (None, "X"))
        monkeypatch.setattr(main_module, "run_forever", lambda runner, config, symbol: None)
        return captured

    def test_config_argument_loads_that_file(self, monkeypatch, tmp_path):
        captured = self._capture(monkeypatch)
        path = tmp_path / "live_wld_long.yaml"
        path.write_text("strategy_path: strategies/weekend_mean_reversion.yaml\nsymbol_override: WLDUSDT\norigin_price: 0.48\n")

        main(["--config", str(path)])

        assert captured["config"].symbol_override == "WLDUSDT"
        assert captured["config"].origin_price == 0.48

    def test_missing_config_file_is_an_error_not_silent_defaults(self, monkeypatch, tmp_path):
        # 打錯檔名時如果默默用預設值跑,會變成跑錯策略/錯的幣。
        self._capture(monkeypatch)
        with pytest.raises(FileNotFoundError):
            main(["--config", str(tmp_path / "typo.yaml")])

    def test_without_argument_uses_default_config(self, monkeypatch):
        import strategy_lab.live.main as main_module

        self._capture(monkeypatch)
        seen = {}
        monkeypatch.setattr(main_module, "load_execution_config", lambda config_path=None: seen.setdefault("path", config_path) or ExecutionConfig())

        main([])

        assert seen["path"] is None
