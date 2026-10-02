"""
live/daemon.py

讓 live/main.py 脫離終端機/PyCharm 在背景跑。2026-09-27 事故:程式跑在
PyCharm 的終端機裡,關掉 PyCharm 時跟著被砍掉,沒有收尾,掛單留在交易所上。

用 start_new_session=True 啟動(= setsid):子程序自己一個 session、沒有
控制終端機,關終端機/PyCharm 送的 SIGHUP 到不了它,也不跟啟動它的腳本同
一個 process group(sat_strategy 的 launchd 排程就是栽在 process group 被
一起殺掉)。停止用 SIGTERM,走 live/main.py 正常的收尾流程。

    python -m strategy_lab.live.daemon start  --config live_btc_band.yaml
    python -m strategy_lab.live.daemon status --config live_btc_band.yaml
    python -m strategy_lab.live.daemon stop   --config live_btc_band.yaml

pid 檔:run/<設定檔名>.pid;終端機輸出:logs/<設定檔名>.console.log
(live/main.py 另外還會寫 logs/<設定檔名>_<啟動時間>.log)。
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "live_execution_config.yaml"
RUN_DIR = PROJECT_ROOT / "run"
LOG_DIR = PROJECT_ROOT / "logs"


class DaemonError(RuntimeError):
    pass


@dataclass
class DaemonInfo:
    pid: int
    pid_file: Path
    console_log: Path


def default_command(config: os.PathLike) -> List[str]:
    return [sys.executable, "-m", "strategy_lab.live.main", "--config", str(config)]


def is_alive(pid: int) -> bool:
    try:
        # 自己啟動的子程序結束後會變成 zombie,kill(pid, 0) 仍然成功;先回收。
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_file(config: Path, run_dir: Path) -> Path:
    return run_dir / f"{config.stem}.pid"


def _console_log(config: Path, log_dir: Path) -> Path:
    return log_dir / f"{config.stem}.console.log"


def _read_pid(pid_file: Path) -> Optional[int]:
    try:
        return int(pid_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _command_line(pid: int) -> str:
    result = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return result.stdout.strip()


def _tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except FileNotFoundError:
        return ""


def start(
    config: os.PathLike,
    run_dir: Path = RUN_DIR,
    log_dir: Path = LOG_DIR,
    command: Optional[List[str]] = None,
    startup_wait: float = 10.0,
) -> DaemonInfo:
    """啟動後等 startup_wait 秒確認沒有立刻結束(例如設定錯誤、啟動檢查
    發現殘留掛單),有的話回報 log 最後幾行,不讓人以為已經在背景跑了。"""
    config = Path(config).resolve()
    if not config.exists():
        raise DaemonError(f"找不到設定檔 {config}")
    run_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    pid_file = _pid_file(config, run_dir)
    existing = _read_pid(pid_file)
    if existing is not None:
        if is_alive(existing):
            raise DaemonError(f"{config.stem} 已經在跑(PID {existing}),不重複啟動")
        pid_file.unlink()

    cmd = command if command is not None else default_command(config)
    console_log = _console_log(config, log_dir)
    with open(console_log, "a", encoding="utf-8") as out:
        out.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 啟動: {' '.join(cmd)} =====\n")
        out.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_file.write_text(f"{proc.pid}\n")

    deadline = time.monotonic() + startup_wait
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pid_file.unlink(missing_ok=True)
            raise DaemonError(
                f"{config.stem} 啟動後立刻結束(exit {proc.returncode}),{console_log} 最後的輸出:\n{_tail(console_log)}"
            )
        time.sleep(0.2)
    return DaemonInfo(pid=proc.pid, pid_file=pid_file, console_log=console_log)


def stop(config: os.PathLike, run_dir: Path = RUN_DIR, timeout: float = 90.0) -> str:
    """送 SIGTERM,等 live/main.py 走完收尾(取消掛單、平倉)才回傳。等不到
    也不會自動 SIGKILL——強制砍掉會跳過收尾,留下沒人管的掛單/部位。"""
    config = Path(config).resolve()
    pid_file = _pid_file(config, run_dir)
    pid = _read_pid(pid_file)
    if pid is None:
        return f"{config.stem} 沒有在跑(找不到 pid 檔)"
    if not is_alive(pid):
        pid_file.unlink(missing_ok=True)
        return f"{config.stem} 沒有在跑(PID {pid} 已經不存在,清掉殘留 pid 檔)"
    if str(config) not in _command_line(pid):
        raise DaemonError(f"PID {pid} 不是這個策略的程式(pid 檔可能過期、PID 被重用),不送停止訊號")

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_alive(pid):
            pid_file.unlink(missing_ok=True)
            return f"{config.stem} 已停止(PID {pid},已走完收尾流程)"
        time.sleep(0.5)
    raise DaemonError(
        f"送出停止訊號 {timeout:.0f} 秒後 PID {pid} 還沒結束——收尾可能還在重試網路,請看 log;不會自動強制砍掉"
    )


def status(config: os.PathLike, run_dir: Path = RUN_DIR, log_dir: Path = LOG_DIR) -> str:
    config = Path(config).resolve()
    pid = _read_pid(_pid_file(config, run_dir))
    if pid is None or not is_alive(pid):
        return f"{config.stem} 沒有在跑"
    return f"{config.stem} 在跑(PID {pid}),終端機輸出: {_console_log(config, log_dir)}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="在背景啟動/停止 strategy_lab live runner")
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="執行設定檔,預設 live_execution_config.yaml")
    args = parser.parse_args(argv)

    try:
        if args.action == "start":
            info = start(args.config)
            print(f"已在背景啟動(PID {info.pid}),關掉終端機/PyCharm 不會影響它。")
            print(f"終端機輸出: {info.console_log}")
            print(f"停止: python -m strategy_lab.live.daemon stop --config {args.config}")
        elif args.action == "stop":
            print(stop(args.config))
        else:
            print(status(args.config))
    except DaemonError as e:
        print(f"[錯誤] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
