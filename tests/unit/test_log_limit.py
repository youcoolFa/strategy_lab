"""log 大小限制器:資料夾總大小超過上限就從最舊的檔開始刪,目前在寫/最近還在寫的檔不刪。"""

import os
import time

from strategy_lab.log.log_limit import enforce_log_limit, max_total_mb_from_env, size_retention

MB = 1024 * 1024


def make_file(directory, name, mb, age_hours):
    path = directory / name
    path.write_bytes(b"x" * int(mb * MB))
    t = time.time() - age_hours * 3600
    os.utime(path, (t, t))
    return path


class TestEnforceLogLimit:
    def test_under_the_limit_nothing_is_deleted(self, tmp_path):
        make_file(tmp_path, "bot.1.log", 1, 48)
        make_file(tmp_path, "bot.2.log", 1, 24)
        assert enforce_log_limit(tmp_path, max_total_mb=5) == []
        assert len(list(tmp_path.iterdir())) == 2

    def test_deletes_oldest_first_until_under_the_limit(self, tmp_path):
        oldest = make_file(tmp_path, "bot.1.log", 2, 72)
        older = make_file(tmp_path, "bot.2.log", 2, 48)
        newer = make_file(tmp_path, "bot.3.log", 2, 24)
        removed = enforce_log_limit(tmp_path, max_total_mb=4.5)
        assert removed == [oldest]
        assert not oldest.exists() and older.exists() and newer.exists()

    def test_protected_and_recently_written_files_are_never_deleted(self, tmp_path):
        current = make_file(tmp_path, "bot.log", 3, 72)  # 正在寫的檔(就算最舊)
        active = make_file(tmp_path, "other.console.log", 3, 0.1)  # 6 分鐘前還有寫入
        old = make_file(tmp_path, "bot.1.log", 3, 48)
        removed = enforce_log_limit(tmp_path, max_total_mb=1, protect=[current])
        assert removed == [old]
        assert current.exists() and active.exists()

    def test_only_matching_files_are_touched(self, tmp_path):
        make_file(tmp_path, "bot.1.log", 3, 72)
        keep = make_file(tmp_path, "notes.txt", 3, 72)
        enforce_log_limit(tmp_path, max_total_mb=1, pattern="bot*.log*")
        assert keep.exists()

    def test_missing_directory_is_fine(self, tmp_path):
        assert enforce_log_limit(tmp_path / "nope", max_total_mb=1) == []


class TestLoguruRetention:
    def test_retention_callable_enforces_the_limit_and_keeps_the_active_file(self, tmp_path):
        active = make_file(tmp_path, "bot.log", 1, 0)
        old = make_file(tmp_path, "bot.2026-08-01.log", 3, 72)
        retention = size_retention(tmp_path, max_total_mb=2, pattern="bot*.log*", protect=[active])
        retention([str(old), str(active)])  # loguru 輪替時呼叫,參數是它找到的 log 檔清單
        assert not old.exists() and active.exists()


class TestLimitFromEnv:
    def test_default_and_override(self, monkeypatch):
        monkeypatch.delenv("LOG_MAX_TOTAL_MB", raising=False)
        assert max_total_mb_from_env(500) == 500
        monkeypatch.setenv("LOG_MAX_TOTAL_MB", "200")
        assert max_total_mb_from_env(500) == 200

    def test_bad_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("LOG_MAX_TOTAL_MB", "abc")
        assert max_total_mb_from_env(500) == 500
        monkeypatch.setenv("LOG_MAX_TOTAL_MB", "0")
        assert max_total_mb_from_env(500) == 500


class TestStrategyLabLoggerSetup:
    """strategy_lab 可能同時有好幾個策略在背景跑:還在跑的(run/<設定檔>.pid 的 PID 還活著)
    那個設定檔的 log 一律不刪,就算超過一小時沒寫入。"""

    def test_keeps_current_and_running_daemon_logs(self, tmp_path, monkeypatch):
        from loguru import logger

        from strategy_lab.log import logger_setup

        logs, run = tmp_path / "logs", tmp_path / "run"
        logs.mkdir()
        run.mkdir()
        (run / "live_wld.pid").write_text(f"{os.getpid()}\n")  # 這個 PID 一定活著
        (run / "live_dead.pid").write_text("999999\n")
        running_run = make_file(logs, "live_wld_20261001_040000.log", 2, 72)
        running_console = make_file(logs, "live_wld.console.log", 2, 72)
        dead_old = make_file(logs, "live_dead_20260927_224108.log", 2, 72)
        own_console = make_file(logs, "live_btc.console.log", 2, 72)
        pending = logs / "db_pending"
        pending.mkdir()
        (pending / "x.jsonl").write_text("{}")
        monkeypatch.setenv("LOG_MAX_TOTAL_MB", "1")

        path, _ = logger_setup.setup_logger("live_btc", log_dir=logs, run_dir=run)
        try:
            assert not dead_old.exists()
            assert running_run.exists() and running_console.exists() and own_console.exists()
            assert path.exists() and (pending / "x.jsonl").exists()
        finally:
            logger.remove()
            logger.add(__import__("sys").stderr)
