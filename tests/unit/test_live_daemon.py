"""live/daemon.py:讓 live/main.py 脫離終端機/PyCharm 在背景跑。用一個
假的長駐程式代替真的 main.py(收到 SIGTERM 就正常結束),不碰網路、不下單。"""

import os
import signal
import subprocess
import sys
import time

import pytest

from strategy_lab.live import daemon

FAKE_MAIN = "import signal,sys,time; signal.signal(signal.SIGTERM, lambda s,f: sys.exit(0)); time.sleep(60)"


def fake_command(config):
    return [sys.executable, "-c", FAKE_MAIN, str(config)]


@pytest.fixture
def env(tmp_path):
    config = (tmp_path / "live_btc_band.yaml").resolve()
    config.write_text("dry_run: true\n")
    run_dir, log_dir = tmp_path / "run", tmp_path / "logs"
    started = []
    yield config, run_dir, log_dir, started
    for pid in started:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass


def start(config, run_dir, log_dir, started, command=None, startup_wait=0.5):
    info = daemon.start(
        config, run_dir=run_dir, log_dir=log_dir, command=command or fake_command(config), startup_wait=startup_wait
    )
    started.append(info.pid)
    return info


class TestStart:
    def test_runs_in_its_own_session_detached_from_any_terminal(self, env):
        config, run_dir, log_dir, started = env

        info = start(config, run_dir, log_dir, started)

        # 自己是 session leader:關掉終端機/PyCharm 送的 SIGHUP 到不了它。
        assert os.getsid(info.pid) == info.pid
        assert daemon.is_alive(info.pid)

    def test_writes_pid_file_named_after_config(self, env):
        config, run_dir, log_dir, started = env

        info = start(config, run_dir, log_dir, started)

        assert info.pid_file == run_dir / "live_btc_band.pid"
        assert info.pid_file.read_text().strip() == str(info.pid)
        assert info.console_log == log_dir / "live_btc_band.console.log"

    def test_refuses_to_start_twice_for_the_same_config(self, env):
        config, run_dir, log_dir, started = env
        start(config, run_dir, log_dir, started)

        with pytest.raises(daemon.DaemonError, match="已經在跑"):
            start(config, run_dir, log_dir, started)

    def test_stale_pid_file_from_a_dead_process_is_replaced(self, env):
        config, run_dir, log_dir, started = env
        run_dir.mkdir(parents=True)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        (run_dir / "live_btc_band.pid").write_text(str(dead.pid))

        info = start(config, run_dir, log_dir, started)

        assert info.pid != dead.pid

    def test_reports_failure_with_log_tail_when_process_exits_immediately(self, env):
        # 例如 ensure_clean_start 拒絕啟動:不能讓使用者以為已經在背景跑了。
        config, run_dir, log_dir, started = env
        failing = [sys.executable, "-c", "import sys; print('拒絕啟動: 有殘留掛單'); sys.exit(1)", str(config)]

        with pytest.raises(daemon.DaemonError, match="拒絕啟動: 有殘留掛單"):
            start(config, run_dir, log_dir, started, command=failing, startup_wait=3.0)

        assert not (run_dir / "live_btc_band.pid").exists()

    def test_missing_config_is_an_error(self, env, tmp_path):
        _, run_dir, log_dir, started = env
        with pytest.raises(daemon.DaemonError, match="找不到設定檔"):
            daemon.start(tmp_path / "typo.yaml", run_dir=run_dir, log_dir=log_dir, command=["true"], startup_wait=0)


class TestStop:
    def test_sends_sigterm_waits_for_exit_and_removes_pid_file(self, env):
        config, run_dir, log_dir, started = env
        info = start(config, run_dir, log_dir, started)

        message = daemon.stop(config, run_dir=run_dir, timeout=10)

        assert "已停止" in message
        assert not daemon.is_alive(info.pid)
        assert not info.pid_file.exists()

    def test_not_running_is_reported_not_raised(self, env):
        config, run_dir, _, _ = env
        assert "沒有在跑" in daemon.stop(config, run_dir=run_dir, timeout=1)

    def test_refuses_to_signal_a_process_that_is_not_this_strategy(self, env):
        # pid 檔過期、PID 被別的程式重用時,不能把 SIGTERM 送給無關的 process。
        config, run_dir, _, started = env
        run_dir.mkdir(parents=True)
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        started.append(other.pid)
        (run_dir / "live_btc_band.pid").write_text(str(other.pid))

        with pytest.raises(daemon.DaemonError, match="不是這個策略"):
            daemon.stop(config, run_dir=run_dir, timeout=1)

        assert daemon.is_alive(other.pid)


class TestStatus:
    def test_reports_running_and_stopped(self, env):
        config, run_dir, log_dir, started = env
        assert "沒有在跑" in daemon.status(config, run_dir=run_dir, log_dir=log_dir)

        info = start(config, run_dir, log_dir, started)
        assert f"PID {info.pid}" in daemon.status(config, run_dir=run_dir, log_dir=log_dir)

        daemon.stop(config, run_dir=run_dir, timeout=10)
        assert "沒有在跑" in daemon.status(config, run_dir=run_dir, log_dir=log_dir)


def test_default_command_runs_live_main_with_the_config():
    cmd = daemon.default_command("/abs/live_btc_band.yaml")
    assert cmd[1:] == ["-m", "strategy_lab.live.main", "--config", "/abs/live_btc_band.yaml"]


class TestDetach:
    def test_old_program_without_handler_is_ended_and_pid_file_removed(self, env):
        """舊版程式沒裝 SIGUSR1 處理器 → 預設動作是直接結束(不收尾),正好是脫離要的效果。"""
        config, run_dir, log_dir, started = env
        info = start(config, run_dir, log_dir, started)
        message = daemon.detach(config, run_dir=run_dir, timeout=10)
        assert "已脫離" in message and not daemon.is_alive(info.pid) and not info.pid_file.exists()

    def test_not_running_is_reported(self, env):
        config, run_dir, _, _ = env
        assert "沒有在跑" in daemon.detach(config, run_dir=run_dir, timeout=1)
