"""每張單的下單/成交/取消都寫進 log(實盤原本只有 dry-run 會記)。"""

from datetime import datetime, timedelta, timezone

import pytest
from loguru import logger

from strategy_lab.engine.scale_in_runner import ScaleInRunner
from strategy_lab.plugins.entry.scale_in import ScaleInEntry
from strategy_lab.plugins.exit.scale_out import ScaleOutExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

NOW = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)


@pytest.fixture
def log_lines():
    lines = []
    sink_id = logger.add(lambda m: lines.append(m.record["message"]), level="INFO")
    yield lines
    logger.remove(sink_id)


def test_place_fill_cancel_and_cleanup_are_logged(log_lines):
    runner = ScaleInRunner(
        entry=ScaleInEntry(weights=[2, 3, 1]),
        exit=ScaleOutExit(distance={"value": 1.0, "unit": "pct"}),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=2.0,
        entry_prices=[1000.0, 990.0, 980.0],
    )
    runner.start(NOW, price=1005.0)
    runner.tick(NOW, 1005.0)
    runner.tick(NOW + timedelta(minutes=1), 999.0)  # 第一注成交
    runner.request_stop()
    runner.tick(NOW + timedelta(minutes=2), 999.0)

    assert any(l.startswith("[下單] event #1 第1注 entry Buy limit @ 1000.0 qty=2") for l in log_lines)
    assert any(l.startswith("[成交] event #1 第1注 entry Buy limit 均價 1000.0 成交量 2") for l in log_lines)
    assert any(l.startswith("[下單] event #1 第1注 exit Sell limit") for l in log_lines)
    assert any(l.startswith("[取消] event #1 第2注 entry") for l in log_lines)
    assert any(l.startswith("開始收尾(stop_requested)") for l in log_lines)
    assert any(l.startswith("[下單] event #1 forced_close Sell market 市價") for l in log_lines)
