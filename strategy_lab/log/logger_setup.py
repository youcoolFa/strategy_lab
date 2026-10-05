"""
strategy_lab/log/logger_setup.py

參考 Fa_Successful_trade/app/log/logger_setup.py 的結構(sat_strategy 也是同一套):
    - console sink(INFO 以上,彩色)
    - 檔案 sink(logs/<設定檔名>_<啟動時間>.log,INFO 以上,一次執行一個檔)
    - Telegram sink(WARNING 以上 = 出問題;加上 logger.bind(telegram=True).info(...)
      標記的重要事件:啟動、成交、每輪損益、結束)。背景發送、冷卻時間、不會自己
      觸發自己,見 telegram_notifier.py。不想發的 WARNING 用 logger.bind(telegram=False)。

Telegram 設定在 .env:TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID(@fa_strategy_lab_bot)。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from loguru import logger

from strategy_lab.log.log_limit import enforce_log_limit, max_total_mb_from_env
from strategy_lab.log.telegram_notifier import TelegramNotifier, telegram_filter

HKT = ZoneInfo("Asia/Hong_Kong")
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
RUN_DIR = Path(__file__).resolve().parents[2] / "run"  # live/daemon.py 的 pid 檔

# logs/ 總大小上限(MB),超過就從最舊的檔刪起,見 log_limit.py;可用 .env 的 LOG_MAX_TOTAL_MB 調整。
DEFAULT_LOG_MAX_TOTAL_MB = 300

# 每個 log 檔寫到這個大小就封存(輪替),封存的檔壓縮成 .log.gz(純文字約剩 1/10)。
# 正在寫的檔不壓縮;一次執行沒寫滿 20 MB 的 log 結束後也維持純文字。
LOG_ROTATION = "20 MB"
LOG_COMPRESSION = "gz"

# Telegram 訊息標題用的名稱(對應 @fa_strategy_lab_bot)
TELEGRAM_PROJECT = "Strategy Lab"

_notifier: Optional[TelegramNotifier] = None


def get_notifier() -> TelegramNotifier:
    """全程式共用一個發送器(冷卻時間、連線狀態才會一致)。第一次用到才建立,
    這時 .env 已經載入;沒有 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 就是停用狀態。"""
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier(project=TELEGRAM_PROJECT)
    return _notifier


def _running_config_names(run_dir: Path) -> Set[str]:
    """run/<設定檔名>.pid 裡的 PID 還活著的設定檔名(背景程式還在跑)。"""
    names = set()
    for pid_file in run_dir.glob("*.pid") if run_dir.is_dir() else []:
        try:
            os.kill(int(pid_file.read_text().strip()), 0)
        except (ValueError, ProcessLookupError, OSError):
            continue
        names.add(pid_file.stem)
    return names


def _protected_logs(log_dir: Path, name: str, current: Path, run_dir: Path) -> List[Path]:
    """不能刪的:這次執行的 log 檔、這個設定檔的 console log,以及還在背景跑的設定檔的所有 log
    (可能好幾個小時沒寫入,但檔案還開著——刪掉之後寫的內容會不見)。"""
    protected = [current, log_dir / f"{name}.console.log"]
    for running in _running_config_names(run_dir) | {name}:
        protected += [log_dir / f"{running}.console.log", *log_dir.glob(f"{running}_*.log*")]
    return protected


def enforce_log_dir_limit(log_dir: Path, name: str, current: Path, run_dir: Path = RUN_DIR) -> None:
    enforce_log_limit(log_dir, max_total_mb_from_env(DEFAULT_LOG_MAX_TOTAL_MB), pattern="*.log*",
                      protect=_protected_logs(log_dir, name, current, run_dir))


def add_file_sink(name: str, log_dir: Optional[Path] = None) -> Tuple[Path, int]:
    """log 寫一份到 logs/<設定檔名>_<啟動時間>.log。終端機一關 log 就沒了
    (2026-09-27 事故只能靠 PyCharm/zsh 的紀錄反推原因)。"""
    directory = log_dir if log_dir is not None else LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}_{datetime.now(HKT).strftime('%Y%m%d_%H%M%S')}.log"
    sink_id = logger.add(path, level="INFO", encoding="utf-8", rotation=LOG_ROTATION, compression=LOG_COMPRESSION,
                         backtrace=True, diagnose=False,
                         retention=lambda _files: enforce_log_dir_limit(directory, name, path))
    return path, sink_id


def setup_logger(name: str, log_dir: Optional[Path] = None, run_dir: Path = RUN_DIR) -> Tuple[Path, int]:
    """換掉 loguru 預設設定,裝上 console / 檔案 / Telegram 三個 sink,並檢查 logs/ 總大小。
    回傳 log 檔路徑與檔案 sink id。"""
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<blue>{name}</blue>:<blue>{function}</blue>:<blue>{line}</blue> | "
            "<level>{message}</level>"
        ),
    )
    directory = log_dir if log_dir is not None else LOG_DIR
    path, sink_id = add_file_sink(name, directory)
    enforce_log_dir_limit(directory, name, path, run_dir)  # 啟動時檢查一次
    logger.add(get_notifier().sink, level="INFO", filter=telegram_filter)
    return path, sink_id
