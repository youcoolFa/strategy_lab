"""live/main.py 的邏輯測試——不需要任何真實 API key、不會發真正的網路
請求。`run_forever()` 的無限迴圈本身,用可注入的假時鐘/假 sleep 讓它在
測試裡跑一段有限的次數,不需要真的等待牆上時間經過。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from strategy_lab.dsl.loader import load_strategy
from strategy_lab.dsl.order_config import PositionSizing
from strategy_lab.engine.runner import RunState
from strategy_lab.live.bybit_client import BybitClient
from strategy_lab.live.config import ExecutionConfig
from strategy_lab.live.main import (
    install_stop_signal_handlers,
    setup_file_logging,
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


@pytest.fixture(autouse=True)
def _log_dir_in_tmp(tmp_path, monkeypatch):
    # main() 會寫 log 檔;測試時寫到 tmp,不污染專案的 logs/。
    import strategy_lab.live.main as main_module

    monkeypatch.setattr(main_module, "LOG_DIR", tmp_path / "logs")
    import strategy_lab.live.preflight as preflight_module

    monkeypatch.setattr(preflight_module, "SNAPSHOT_DIR", tmp_path / "run")


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
        config = ExecutionConfig(strategy_path="strategies/mean_reversion_breakout_guard.yaml", dry_run=True, testnet=True)

        runner, symbol = build_runner_and_symbol(config)

        expected = to_bybit_symbol(load_strategy(config.strategy_path).symbol)
        assert symbol == expected
        assert runner.broker.symbol == expected
        assert runner.broker.dry_run is True
        assert runner.order_qty == 1.0  # 預設 position_sizing 是 fixed_qty, value=1.0
        assert runner.order_type == "limit"  # 預設值,對齊 ExecutionConfig.order_type

    def test_order_type_market_is_threaded_through_to_runner(self, monkeypatch):
        # mean_reversion_breakout_guard 是一啟動就掛單的機制,只接受 limit;
        # 這裡用 ma_crossover_bracket 驗證 market 有被傳進 runner。
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/ma_crossover_bracket.yaml", order_type="market")

        runner, _ = build_runner_and_symbol(config)

        assert runner.order_type == "market"

    def test_weekend_strategy_with_market_order_type_is_rejected(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/mean_reversion_breakout_guard.yaml", order_type="market")

        with pytest.raises(ValueError, match="limit"):
            build_runner_and_symbol(config)

    def test_symbol_override_takes_precedence_over_yaml(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(
            strategy_path="strategies/mean_reversion_breakout_guard.yaml", symbol_override="ETHUSDT", dry_run=True
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
        config = ExecutionConfig(strategy_path="strategies/mean_reversion_breakout_guard.yaml", category="spot")

        build_runner_and_symbol(config)

        assert captured["category"] == "spot"

    def test_dry_run_false_still_builds_without_real_network_call(self, monkeypatch):
        """建構 BybitClient(即使 dry_run=False)本身不應該發網路請求
        ——只是準備好連線設定,實際下單才會真的呼叫出去。"""
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/mean_reversion_breakout_guard.yaml", dry_run=False, testnet=True)

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
            strategy_path="strategies/mean_reversion_breakout_guard.yaml",
            position_sizing=PositionSizing(mode="fixed_qty", value=0.02),
        )

        runner, _ = build_runner_and_symbol(config)

        assert runner.order_qty == 0.02

    def test_fixed_quote_amount_mode_uses_current_price(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        monkeypatch.setattr(BybitClient, "get_last_price", lambda self, symbol: 50000.0)
        config = ExecutionConfig(
            strategy_path="strategies/mean_reversion_breakout_guard.yaml",
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
            strategy_path="strategies/mean_reversion_breakout_guard.yaml",
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
            strategy_path="strategies/mean_reversion_breakout_guard.yaml",
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
        config = ExecutionConfig(strategy_path="strategies/mean_reversion_breakout_guard.yaml", dry_run=True, poll_interval_seconds=0)
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
            strategy_path="strategies/mean_reversion_breakout_guard.yaml",
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
        config = ExecutionConfig(strategy_path="strategies/mean_reversion_breakout_guard.yaml", dry_run=False, testnet=True)
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
        monkeypatch.setattr(main_module, "run_forever", lambda runner, config, symbol, **kwargs: None)
        return captured

    def test_config_argument_loads_that_file(self, monkeypatch, tmp_path):
        captured = self._capture(monkeypatch)
        path = tmp_path / "live_wld_long.yaml"
        path.write_text("strategy_path: strategies/mean_reversion_breakout_guard.yaml\nsymbol_override: WLDUSDT\norigin_price: 0.48\n")

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


class StopRecorder:
    def __init__(self):
        self.stop_requested = False

    def request_stop(self):
        self.stop_requested = True


class TestStopSignalHandlers:
    """2026-09-27 事故:關掉 PyCharm 時終端機送 SIGHUP,原本只處理
    SIGINT/SIGTERM,程式直接被砍掉、沒收尾,WLD 掛單留在交易所上。"""

    def _install(self, monkeypatch):
        import signal

        import strategy_lab.live.main as main_module

        registered = {}
        detached = []
        monkeypatch.setattr(main_module.signal, "signal", lambda sig, handler: registered.__setitem__(sig, handler))
        monkeypatch.setattr(main_module, "_detach_from_closed_terminal", lambda: detached.append(True))
        runner = StopRecorder()
        install_stop_signal_handlers(runner)
        return signal, registered, detached, runner

    def test_sighup_sigint_sigterm_are_all_handled(self, monkeypatch):
        signal, registered, _, _ = self._install(monkeypatch)
        assert {signal.SIGHUP, signal.SIGINT, signal.SIGTERM} <= set(registered)

    def test_sighup_requests_normal_cleanup_and_detaches_from_the_dead_terminal(self, monkeypatch):
        signal, registered, detached, runner = self._install(monkeypatch)

        registered[signal.SIGHUP](signal.SIGHUP, None)

        assert runner.stop_requested is True
        assert detached == [True]  # 終端機已經關了,之後寫 stdout/stderr 會出錯

    def test_ctrl_c_requests_cleanup_without_detaching(self, monkeypatch):
        signal, registered, detached, runner = self._install(monkeypatch)

        registered[signal.SIGINT](signal.SIGINT, None)

        assert runner.stop_requested is True
        assert detached == []


class TestFileLogging:
    def test_log_file_is_named_after_the_config_and_receives_messages(self, tmp_path):
        from loguru import logger

        path, sink_id = setup_file_logging("live_btc_band", log_dir=tmp_path)
        try:
            logger.info("hello-file-log")
        finally:
            logger.remove(sink_id)

        assert path.parent == tmp_path
        assert path.name.startswith("live_btc_band_") and path.suffix == ".log"
        assert "hello-file-log" in path.read_text(encoding="utf-8")

    def test_main_writes_crash_traceback_into_the_log_file(self, monkeypatch, tmp_path):
        import strategy_lab.live.main as main_module

        log_dir = tmp_path / "crash_logs"
        monkeypatch.setattr(main_module, "LOG_DIR", log_dir)
        monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
        monkeypatch.setattr(main_module, "build_runner_and_symbol", lambda config: (None, "X"))

        def boom(runner, config, symbol, **kwargs):
            raise RuntimeError("network-boom")

        monkeypatch.setattr(main_module, "run_forever", boom)
        path = tmp_path / "live_wld_long.yaml"
        path.write_text("dry_run: true\n")

        with pytest.raises(RuntimeError, match="network-boom"):
            main(["--config", str(path)])

        logs = list(log_dir.glob("live_wld_long_*.log"))
        assert len(logs) == 1
        text = logs[0].read_text(encoding="utf-8")
        assert "network-boom" in text and "Traceback" in text


class TestLoopWiring:
    def test_strategy_loop_and_event_logger_are_passed_to_runner(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/mean_reversion_breakout_guard.yaml", dry_run=True)

        runner, _ = build_runner_and_symbol(config)

        assert runner.loop == load_strategy(config.strategy_path).loop
        assert runner.on_event is not None


class RecorderSpy:
    def __init__(self):
        self.started, self.ended, self.orders, self.events = [], [], [], []

    def start_run(self, info):
        self.started.append(info)
        return "run-1"

    def record_order(self, rec):
        self.orders.append(rec)

    def record_event(self, event):
        self.events.append(event)

    def end_run(self, now, reason, summary=None):
        self.ended.append(reason)


class TestRecorderWiring:
    def _runner(self, monkeypatch, recorder, dry_run=False):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/mean_reversion_breakout_guard.yaml", dry_run=dry_run, testnet=True, poll_interval_seconds=0)
        runner, symbol = build_runner_and_symbol(config, recorder=recorder)
        return config, runner, symbol

    def test_refused_start_is_recorded_as_a_run_with_reason(self, monkeypatch):
        spy = RecorderSpy()
        config, runner, symbol = self._runner(monkeypatch, spy)
        runner.broker.client = FakeExchangeClient(open_orders=[{"orderId": "old", "side": "Buy", "price": "1", "qty": "1"}])

        with pytest.raises(LeftoverExchangeStateError):
            run_forever(runner, config, symbol, now_fn=lambda: datetime(2026, 8, 1, 4, 0, tzinfo=HKT), recorder=spy)

        assert len(spy.started) == 1 and spy.ended == ["refused_leftover"]

    def test_normal_stop_records_runner_stop_reason(self, monkeypatch):
        import strategy_lab.live.main as main_module

        spy = RecorderSpy()
        config, runner, symbol = self._runner(monkeypatch, spy)
        runner.broker.client = FakeExchangeClient()

        def price(runner, config, symbol):
            runner.request_stop()
            return 1000.0

        monkeypatch.setattr(main_module, "get_current_price", price)
        monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
        run_forever(runner, config, symbol, now_fn=lambda: datetime(2026, 8, 1, 4, 0, tzinfo=HKT), recorder=spy)

        assert spy.ended == ["stop_requested"]
        assert spy.started[0].strategy_name == "mean_reversion_breakout_guard"
        assert spy.started[0].origin_price == 1000.0

    def test_runner_orders_and_events_flow_into_recorder(self, monkeypatch):
        spy = RecorderSpy()
        _, runner, _ = self._runner(monkeypatch, spy)
        assert runner.on_order == spy.record_order

    def test_dry_run_never_builds_a_recorder(self, monkeypatch):
        import strategy_lab.live.main as main_module

        assert main_module.make_recorder(ExecutionConfig(dry_run=True), client=None) is None


class TestScaleInWiring:
    def test_scale_in_strategy_builds_scale_in_runner_with_entry_prices(self, monkeypatch):
        from strategy_lab.engine.scale_in_runner import ScaleInRunner

        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/scale_in_ladder.yaml", dry_run=True,
                                 entry_prices=[1000.0, 990.0, 980.0],
                                 position_sizing=PositionSizing(mode="fixed_qty", value=0.004))

        runner, _ = build_runner_and_symbol(config)

        assert isinstance(runner, ScaleInRunner)
        assert runner.entry_prices == [1000.0, 990.0, 980.0]
        assert runner.order_qty == 0.004

    def test_scale_in_without_entry_prices_is_an_error(self, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/scale_in_ladder.yaml", dry_run=True)
        with pytest.raises(ValueError, match="entry_prices"):
            build_runner_and_symbol(config)


BAND_YAML = """name: band_example
symbol: BTC/USDT
direction: both
loop: null
scale_in: false
band: true
entry:
  type: band
  params: {buy_pct: 0.5, sell_pct: 0.4}
exit:
  type: band
  params: {}
time_window:
  type: weekly_window
  params: {end_weekday: 0, end_time: "06:00"}
"""


class TestBandWiring:
    """區間策略(2026-10-10):band: true → BandRunner;不支援接手現有持倉;Telegram 成交訊息標「區間」。"""

    def _config(self, tmp_path, **kw):
        path = tmp_path / "band.yaml"
        path.write_text(BAND_YAML)
        return ExecutionConfig(strategy_path=str(path), dry_run=True, testnet=True, symbol_override="XRPUSDT",
                               position_sizing=PositionSizing(mode="fixed_qty", value=10), **kw)

    def test_band_strategy_builds_a_band_runner(self, monkeypatch, tmp_path):
        from strategy_lab.engine.band_runner import BandRunner

        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        runner, symbol = build_runner_and_symbol(self._config(tmp_path))
        assert isinstance(runner, BandRunner) and symbol == "XRPUSDT"
        assert runner.order_qty == 10 and runner.direction == "both" and runner.loop is None
        assert runner.entry.prices(100.0) == (pytest.approx(99.5), pytest.approx(100.4))

    def test_adopting_existing_position_is_refused_for_band(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        with pytest.raises(ValueError, match="接手"):
            build_runner_and_symbol(self._config(tmp_path, adopt_existing_position=True))

    def test_band_fill_is_labelled_in_telegram(self):
        from loguru import logger

        from strategy_lab.engine.runner import OrderRecord
        from strategy_lab.live.main import attach_notifications
        from strategy_lab.log.telegram_notifier import telegram_filter

        sent = []
        sink = logger.add(lambda m: sent.append(m.record["message"]), level="DEBUG", filter=telegram_filter)
        try:
            class R:
                on_order = on_event = None
                loop = None
                events = []

            runner = R()
            attach_notifications(runner, dry_run=False)
            runner.on_order(OrderRecord(order_id="o", purpose="band", event_index=1, side="Sell", order_type="limit",
                                        price=101.0, qty=2.0, reduce_only=False, status="closed", avg_price=101.0,
                                        filled_qty=2.0, time=datetime(2026, 8, 1, tzinfo=ZoneInfo("UTC"))))
        finally:
            logger.remove(sink)
        assert "區間" in sent[0] and "band" not in sent[0]
