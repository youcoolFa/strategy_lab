"""strategy_lab/log/logger_setup.py:跟 Fa_Successful_trade 同一個結構——終端機 INFO、
每次執行一個 log 檔、Telegram(WARNING 以上 + 標記的事件)。"""

from loguru import logger

from strategy_lab.log import logger_setup


class FakeNotifier:
    def __init__(self):
        self.records = []

    def sink(self, message):
        self.records.append(message.record)


def test_console_file_and_telegram_sinks(tmp_path, monkeypatch):
    fake = FakeNotifier()
    monkeypatch.setattr(logger_setup, "_notifier", fake)
    path, _ = logger_setup.setup_logger("live_btc", log_dir=tmp_path)
    try:
        logger.info("一般 INFO")
        logger.warning("出問題")
        logger.bind(telegram=True).info("重要事件")
        logger.bind(telegram=False).warning("不發的 WARNING")
        assert "一般 INFO" in path.read_text(encoding="utf-8")
        assert [r["message"] for r in fake.records] == ["出問題", "重要事件"]
    finally:
        logger.remove()
        logger.add(__import__("sys").stderr)


def test_without_token_telegram_is_disabled():
    assert logger_setup.get_notifier().enabled is False


def test_rotated_logs_are_compressed(tmp_path, monkeypatch):
    import gzip
    import sys

    monkeypatch.setattr(logger_setup, "LOG_ROTATION", "2 KB")
    path, _ = logger_setup.setup_logger("live_btc", log_dir=tmp_path, run_dir=tmp_path / "run")
    try:
        for i in range(100):
            logger.info(f"第 {i} 行 " + "x" * 50)
        archives = sorted(tmp_path.glob("live_btc_*.log.gz"))
        assert archives
        assert "第 0 行" in gzip.open(archives[0], "rt").read()
        assert path.exists()
    finally:
        logger.remove()
        logger.add(sys.stderr)
