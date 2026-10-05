"""live 執行時會發到 Telegram 的事件訊息(telegram=True 的 INFO + WARNING 以上)。
用 telegram_filter 接一個記憶體 sink 檢查,不碰網路、不發真訊息。"""

from datetime import datetime, timedelta

import pytest
from loguru import logger

from strategy_lab.engine.events import Event
from strategy_lab.engine.runner import OrderRecord
from strategy_lab.live.config import ExecutionConfig
from strategy_lab.live.main import HKT, attach_notifications, build_runner_and_symbol, mode_label, run_forever
from strategy_lab.log.telegram_notifier import telegram_filter

NOW = datetime(2026, 8, 1, 4, 0, tzinfo=HKT)


@pytest.fixture
def telegram():
    sent = []
    sink = logger.add(lambda m: sent.append(m.record["message"]), level="DEBUG", filter=telegram_filter)
    yield sent
    logger.remove(sink)


class TestModeLabel:
    def test_labels(self):
        assert "DRY RUN" in mode_label(ExecutionConfig(dry_run=True))
        assert mode_label(ExecutionConfig(dry_run=False, testnet=True)) == "測試網"
        assert mode_label(ExecutionConfig(dry_run=False, testnet=False)) == "實盤"


class TestRunForeverStartAndEnd:
    def test_sends_start_and_end_summary(self, monkeypatch, telegram):
        import strategy_lab.live.main as main_module

        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/weekend_mean_reversion.yaml", dry_run=True, poll_interval_seconds=0)
        runner, symbol = build_runner_and_symbol(config)
        prices = iter([1000.0, 989.0, 989.0, 1000.0, 1000.0])
        clock = {"now": NOW}

        def fake_get_price(runner, config, symbol):
            clock["now"] += timedelta(minutes=5)
            try:
                return next(prices)
            except StopIteration:
                runner.request_stop()
                return 1000.0

        monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
        monkeypatch.setattr(main_module, "get_current_price", fake_get_price)

        run_forever(runner, config, symbol, now_fn=lambda: clock["now"])

        start, end = telegram[0], telegram[-1]
        assert "啟動" in start and "DRY RUN" in start and "weekend_mean_reversion" in start and symbol in start
        assert "結束" in end and "stop_requested" in end and "完成 1 個 loop" in end
        # 收尾時的「開始收尾」WARNING 不另外發(結束訊息已經有原因)
        assert not any("開始收尾" in m for m in telegram)


def record(status, purpose="entry", lot=None):
    return OrderRecord(order_id="o1", purpose=purpose, event_index=1, side="Buy", order_type="limit", price=84643.5,
                       qty=0.002, reduce_only=False, status=status, avg_price=84643.5 if status == "closed" else None,
                       filled_qty=0.002 if status == "closed" else 0.0, time=NOW, lot=lot)


def event():
    return Event(index=1, direction="long", start_time=NOW, end_time=NOW + timedelta(hours=2), fills=2,
                 max_position=0.002, avg_entry=84643.5, avg_exit=85283.1, realized_pnl=1.2792, max_drawdown=-0.5,
                 forced=False)


class FakeRunner:
    def __init__(self):
        self.on_order = None
        self.on_event = None
        self.loop = 2
        self.events = []


class TestAttachNotifications:
    def test_live_sends_fills_and_event_results_and_keeps_existing_callbacks(self, telegram):
        runner = FakeRunner()
        seen = []
        runner.on_order = lambda r: seen.append(("order", r.status))
        runner.on_event = lambda e: seen.append(("event", e.index))
        attach_notifications(runner, dry_run=False)

        runner.on_order(record("open"))
        runner.on_order(record("closed", lot=2))
        runner.on_event(event())

        assert seen == [("order", "open"), ("order", "closed"), ("event", 1)]  # 原本的紀錄照常
        assert len(telegram) == 2  # 掛單不發;成交、event 完成各一則
        assert "成交" in telegram[0] and "進場" in telegram[0] and "第2注" in telegram[0] and "84643.5" in telegram[0]
        assert "第 1 個 loop(共 3 個)" in telegram[0]
        assert "第 1 個 loop(共 3 個) 完成" in telegram[1] and "這輪損益 +1.2792" in telegram[1]
        assert "累計 +1.2792 USDT(1 個 loop)" in telegram[1]

    def test_dry_run_does_not_send_trade_events(self, telegram):
        runner = FakeRunner()
        attach_notifications(runner, dry_run=True)
        if runner.on_order:
            runner.on_order(record("closed"))
        if runner.on_event:
            runner.on_event(event())
        assert telegram == []


class TestCategories:
    def test_fills_and_event_results_have_their_own_categories(self):
        records = []
        sink = logger.add(lambda m: records.append(m.record["extra"].get("category")), level="DEBUG", filter=telegram_filter)
        try:
            runner = FakeRunner()
            attach_notifications(runner, dry_run=False)
            runner.on_order(record("closed"))
            runner.on_event(event())
        finally:
            logger.remove(sink)
        assert records == ["fill", "pnl"]


class TestHeartbeatInRunForever:
    def test_status_is_reported_every_interval_while_running(self, monkeypatch, telegram):
        import strategy_lab.live.main as main_module

        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        monkeypatch.setenv("STATUS_INTERVAL_MINUTES", "60")
        config = ExecutionConfig(strategy_path="strategies/weekend_mean_reversion.yaml", dry_run=True, poll_interval_seconds=0)
        runner, symbol = build_runner_and_symbol(config)
        clock = {"now": NOW, "ticks": 0}

        def fake_get_price(runner, config, symbol):
            clock["now"] += timedelta(minutes=30)
            clock["ticks"] += 1
            if clock["ticks"] > 5:  # 約 2.5 小時後停止
                runner.request_stop()
            return 1000.0

        monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
        monkeypatch.setattr(main_module, "get_current_price", fake_get_price)
        run_forever(runner, config, symbol, now_fn=lambda: clock["now"])

        statuses = [m for m in telegram if m.startswith("⏱")]
        assert len(statuses) == 2  # 第 60、120 分鐘
        assert "運作中" in statuses[0] and "第 1 個 loop" in statuses[0]
