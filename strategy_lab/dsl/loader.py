"""
把 YAML 策略檔變成真正可以塞進 StrategyRunner 的物件。

流程:讀檔 -> yaml.safe_load 成 dict -> StrategyDefinition(**dict) 驗證
形狀(不合法在這裡就報錯,不會進到下一步)-> 對每個區塊用
registry.get(kind, spec.type) 把字串轉成 class -> class(**spec.params)
建構出真正的 plugin 物件。

這是 registry.get() 第一次真正被呼叫的地方——之前(Phase 1、2)demo 都是
直接 import 具體 class,registry 那一側等於是死碼。這裡開始,plugin
名稱不再是 Python import,是 YAML 裡的一個字串。

只支援 `{type, params}` 這個最簡單的形狀:每個 plugin 已經會在自己的
`__post_init__` 裡用 `params` 組出 `self.rule`,loader 不需要、也沒有
再另外解析一棵獨立的 Condition 樹——這是刻意收斂的範圍,見對話紀錄。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import yaml

import strategy_lab.plugins  # noqa: F401  (觸發所有 @register 執行)
from strategy_lab.dsl.schema import StrategyDefinition
from strategy_lab.interfaces import EntrySignal, ExitSignal, KillSwitch, TimeWindow
from strategy_lab.registry import get as registry_get


@dataclass
class ComposedStrategy:
    name: str
    symbol: str
    order_qty: float
    entry: EntrySignal
    exit: ExitSignal
    time_window: TimeWindow
    kill_switch: Optional[KillSwitch] = None


def load_strategy(path: Union[str, Path]) -> ComposedStrategy:
    raw = yaml.safe_load(Path(path).read_text())
    definition = StrategyDefinition(**raw)

    return ComposedStrategy(
        name=definition.name,
        symbol=definition.symbol,
        order_qty=definition.order_qty,
        entry=registry_get("entry", definition.entry.type)(**definition.entry.params),
        exit=registry_get("exit", definition.exit.type)(**definition.exit.params),
        time_window=registry_get("time_window", definition.time_window.type)(**definition.time_window.params),
        kill_switch=(
            registry_get("kill_switch", definition.kill_switch.type)(**definition.kill_switch.params)
            if definition.kill_switch is not None
            else None
        ),
    )
