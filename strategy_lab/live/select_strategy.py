"""
live/select_strategy.py

互動式小工具:列出 strategies/*.yaml,讓使用者選一個,把選擇寫進
live_execution_config.yaml 的 strategy_path 欄位——只改這一個欄位,其餘
既有設定(dry_run/testnet 等)維持不變。

刻意不是 live/main.py 的一部分:live/main.py 必須能無人值守啟動(被
launchd/systemd 之類的排程器叫起來時不會有人在終端機前守著),用
input() 會讓它卡死在那裡等一個永遠不會來的輸入。這個工具反過來是設計
成「人在電腦前,先跑一次選好策略」,選完就結束,不會常駐——跟
live/main.py 的無人值守定位是兩件事,分開成兩個檔案。

執行方式:.venv/bin/python -m strategy_lab.live.select_strategy
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import List, Optional

import yaml

from strategy_lab.dsl.discovery import list_strategy_files, prompt_strategy_choice

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STRATEGIES_DIR = PROJECT_ROOT / "strategies"
CONFIG_PATH = PROJECT_ROOT / "live_execution_config.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "live_execution_config.example.yaml"


# 頂層的 strategy_path 那一行:值 + 可能有的行尾註解
_STRATEGY_PATH_LINE = re.compile(r"^strategy_path:[ \t]*[^#\n]*?(?P<comment>[ \t]+#[^\n]*)?$", re.MULTILINE)


def update_strategy_path_in_config(config_path: Path, example_path: Path, new_strategy_path: str) -> None:
    """config_path 存在就讀它、只改 strategy_path 那一行;不存在就用
    example_path 當起點(而不是無中生有一份空的),確保其他欄位
    (dry_run/testnet 等)有安全預設值,不會漏欄位。

    逐行替換而不是 yaml.load → yaml.dump:dump 會把整份檔案重寫,所有註解
    (每個欄位的說明、「2026-09-26 你的明確指示」這類紀錄)全部消失。那一行
    的行尾註解也保留。"""
    source = config_path if config_path.exists() else example_path
    text = source.read_text(encoding="utf-8")

    new_line = f"strategy_path: {new_strategy_path}"
    match = _STRATEGY_PATH_LINE.search(text)
    if match:
        text = text[: match.start()] + new_line + (match.group("comment") or "") + text[match.end():]
    else:
        text = (text if not text or text.endswith("\n") else text + "\n") + new_line + "\n"
    if not text.endswith("\n"):
        text += "\n"

    # 寫入前確認改出來的還是合法 YAML、值也對,不對就不寫,不弄壞設定檔
    data = yaml.safe_load(text) or {}
    if data.get("strategy_path") != new_strategy_path:
        raise ValueError(f"無法安全地改寫 {source} 的 strategy_path,請手動修改")

    config_path.write_text(text, encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="互動選策略,寫進執行設定檔的 strategy_path")
    parser.add_argument(
        "--config",
        default=None,
        help="要寫入的設定檔;不存在就從 example 範本建立。不給就用 live_execution_config.yaml",
    )
    args = parser.parse_args(argv)
    config_path = Path(args.config) if args.config else CONFIG_PATH

    strategy_files = list_strategy_files(STRATEGIES_DIR)
    if not strategy_files:
        print(f"{STRATEGIES_DIR} 底下沒有任何 *.yaml,無法選擇。")
        return

    chosen = prompt_strategy_choice(strategy_files)
    relative_path = chosen.relative_to(PROJECT_ROOT).as_posix()

    update_strategy_path_in_config(config_path, EXAMPLE_CONFIG_PATH, relative_path)
    print(f"已將 {config_path.name} 的 strategy_path 設為:{relative_path}")
    run_hint = "" if args.config is None else f" --config {args.config}"
    print(f"下次執行 python3 -m strategy_lab.live.main{run_hint} 就會使用這個策略。")


if __name__ == "__main__":
    main()
