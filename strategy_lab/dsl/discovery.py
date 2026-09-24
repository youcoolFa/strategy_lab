"""列出 strategies/*.yaml、提供互動式選單讓使用者選一個——給
demo/run_from_yaml.py 和 live/select_strategy.py 共用,避免兩邊各寫一套
一樣的「掃描目錄 + 印選單 + 讀輸入」邏輯。

`input_fn`/`print_fn` 用依賴注入(預設分別是內建的 `input`/`print`),
測試時可以換成假的函式,不用真的等鍵盤輸入——跟這個 repo 其他地方
`now_fn`/`http_client` 那種可測試性做法一致。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List


def list_strategy_files(strategies_dir: Path) -> List[Path]:
    """回傳 strategies_dir 底下所有 *.yaml,按檔名排序(順序穩定,不受
    檔案系統列出順序影響)。"""
    return sorted(strategies_dir.glob("*.yaml"))


def prompt_strategy_choice(
    strategy_files: List[Path],
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> Path:
    """印出編號選單,讀使用者輸入的數字,回傳對應的檔案路徑。輸入不是
    數字、或超出範圍,會重新問,不會直接崩潰或悄悄選錯策略。"""
    if not strategy_files:
        raise ValueError("strategies_dir 底下沒有任何 *.yaml,無法選擇")

    print_fn("可用策略：")
    for i, path in enumerate(strategy_files, start=1):
        print_fn(f"  {i}. {path.stem}")

    while True:
        raw = input_fn("請輸入數字選擇要跑的策略：")
        try:
            choice = int(raw)
        except ValueError:
            print_fn(f"「{raw}」不是數字,請重新輸入。")
            continue
        if 1 <= choice <= len(strategy_files):
            return strategy_files[choice - 1]
        print_fn(f"請輸入 1 到 {len(strategy_files)} 之間的數字。")
