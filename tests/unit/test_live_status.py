"""Telegram 狀態訊息(live/status.py):啟動訊息帶參數、每小時狀態回報(心跳)、loop 編號與累計損益。
用 PaperBroker 驅動真的 runner,不碰網路。"""

from datetime import datetime, timedelta

import pytest
from loguru import logger

from strategy_lab.engine.scale_in_runner import ScaleInRunner
from strategy_lab.live.config import ExecutionConfig
from strategy_lab.live.status import (
    StatusReporter,
    loop_progress,
    start_message,
    status_interval_minutes,
    status_message,
    stop_reason_label,
)
from strategy_lab.log.telegram_notifier import telegram_filter
from strategy_lab.plugins.entry.scale_in import ScaleInEntry
from strategy_lab.plugins.exit.scale_out import ScaleOutExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow
from zoneinfo import ZoneInfo

HKT = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 10, 4, 0, tzinfo=HKT)
CONFIG = ExecutionConfig(strategy_path="strategies/scale_in_ladder.yaml", dry_run=False, testnet=False)


def scale_runner(loop=0):
    runner = ScaleInRunner(
        entry=ScaleInEntry(weights=[2, 3, 1]), exit=ScaleOutExit(distance={"value": 1.0, "unit": "pct"}),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=0.002, entry_prices=[84900.0, 84500.0, 84000.0], loop=loop,
    )
    runner.start(NOW, 85292.4)
    return runner


class TestLoopProgress:
    def test_labels(self):
        assert loop_progress(1, 0) == "第 1 個 loop(共 1 個)"
        assert loop_progress(2, 2) == "第 2 個 loop(共 3 個)"
        assert loop_progress(5, None) == "第 5 個 loop(不限次數)"

    def test_stop_reasons_in_chinese(self):
        assert "窗口" in stop_reason_label("window_cleanup")
        assert "loop" in stop_reason_label("loop_done")
        assert "停止訊號" in stop_reason_label("stop_requested")
        assert stop_reason_label(None) == "未知"


class TestStartMessage:
    def test_scale_in_start_lists_parameters_lots_exits_and_window(self):
        runner = scale_runner()
        msg = start_message(runner, CONFIG, "BTCUSDT", 85292.4, NOW, interval_minutes=60)
        assert "啟動(實盤)" in msg and "scale_in_ladder(long)" in msg and "BTCUSDT" in msg
        assert "weights" in msg and "distance" in msg  # 策略參數
        assert "第1注 84900 × 0.002 → 平倉 85749" in msg
        assert "依序掛單" in msg
        assert "最大部位 0.006" in msg
        assert "共 1 個" in msg
        assert "收尾 2026-10-12 05:55" in msg  # 週一 06:00 前 5 分鐘
        assert "每 60 分鐘" in msg


class TestStatusMessage:
    def test_waiting_for_entry(self):
        runner = scale_runner()
        runner.tick(NOW, 85292.4)  # 掛上三注
        msg = status_message(runner, CONFIG, "BTCUSDT", 85100.0, NOW + timedelta(hours=1), started_at=NOW)
        assert "運作中(實盤)" in msg and "已運行 1 小時 0 分" in msg
        assert "等待進場" in msg and "第 1 個 loop(共 1 個)" in msg
        assert "進場 Buy 0.002 @ 84900(第1注,距現價 -0.24%)" in msg
        assert "待掛(前一注成交後才掛):第2注 84500 × 0.003、第3注 84000 × 0.001" in msg
        assert "已完成 0 個 loop|累計毛利 +0.00 USDT" in msg
        assert "收尾還有" in msg

    def test_holding_shows_position_unrealized_and_exit_orders(self):
        runner = scale_runner()
        runner.tick(NOW, 85292.4)
        runner.tick(NOW + timedelta(minutes=5), 84800.0)  # 第一注成交
        msg = status_message(runner, CONFIG, "BTCUSDT", 85000.0, NOW + timedelta(hours=2), started_at=NOW)
        assert "持倉中" in msg
        assert "部位 多 0.002 @ 84900|未實現 +0.20" in msg  # (85000 − 84900) × 0.002
        assert "平倉 Sell 0.002 @ 85749(第1注" in msg
        assert "進場 Buy 0.003 @ 84500(第2注" in msg  # 第一注成交後已掛上
        assert "待掛(前一注成交後才掛):第3注 84000 × 0.001" in msg


class TestStatusReporter:
    def test_reports_every_interval_and_can_be_disabled(self):
        sent = []
        sink = logger.add(lambda m: sent.append(m.record), level="DEBUG", filter=telegram_filter)
        try:
            runner = scale_runner()
            runner.tick(NOW, 85292.4)
            reporter = StatusReporter(runner, CONFIG, "BTCUSDT", started_at=NOW, interval_minutes=60)
            reporter.maybe_report(NOW + timedelta(minutes=59), 85000.0)
            reporter.maybe_report(NOW + timedelta(minutes=60), 85000.0)
            reporter.maybe_report(NOW + timedelta(minutes=90), 85000.0)
            reporter.maybe_report(NOW + timedelta(minutes=121), 85000.0)
            off = StatusReporter(runner, CONFIG, "BTCUSDT", started_at=NOW, interval_minutes=0)
            off.maybe_report(NOW + timedelta(days=1), 85000.0)
        finally:
            logger.remove(sink)
        assert len(sent) == 2
        assert all(r["extra"]["category"] == "status" for r in sent)

    def test_interval_from_env(self, monkeypatch):
        monkeypatch.delenv("STATUS_INTERVAL_MINUTES", raising=False)
        assert status_interval_minutes() == 60
        monkeypatch.setenv("STATUS_INTERVAL_MINUTES", "30")
        assert status_interval_minutes() == 30
        monkeypatch.setenv("STATUS_INTERVAL_MINUTES", "0")
        assert status_interval_minutes() == 0
        monkeypatch.setenv("STATUS_INTERVAL_MINUTES", "abc")
        assert status_interval_minutes() == 60


class TestStatusNet:
    def test_status_shows_cumulative_net_when_costs_are_known(self):
        class Costs:
            def event_costs(self, index):
                return (0.03, 0.0)

        runner = scale_runner(loop=2)
        runner.tick(NOW, 85292.4)
        runner.tick(NOW + timedelta(minutes=5), 84800.0)  # 第一注成交
        runner.tick(NOW + timedelta(minutes=10), 85800.0)  # 平倉 → 第 1 個 loop:毛利 (85749 − 84900) × 0.002 = 1.698
        msg = status_message(runner, CONFIG, "BTCUSDT", 85800.0, NOW + timedelta(hours=1), started_at=NOW, costs=Costs())
        assert "已完成 1 個 loop|累計淨利 +1.67 USDT(已扣手續費、資金費)|手續費佔利益 1.77%" in msg


class TestNumberFormat:
    def test_prices_have_at_most_4_decimals_and_amounts_2(self):
        from strategy_lab.live.status import px, usd

        assert px(1.1947847999999999) == "1.1948"
        assert px(1.2310) == "1.231" and px(84900.0) == "84900" and px(None) == "—"
        assert usd(0.11535) == "+0.12" and usd(-0.2) == "-0.20"
