"""
YAML 策略檔的 schema(pydantic v1)。只負責「這個 dict 合不合法」——
把 `type` 字串解析成實際的 plugin class,是 dsl/loader.py 的職責,這裡
完全不 import registry,也不知道有哪些 plugin 真的存在。

`extra = "forbid"`(對 PluginSpec 跟 StrategyDefinition 都是)是刻意的:
YAML 打錯欄位名這種手誤,要在讀檔的當下就報錯,不能悄悄被忽略。
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, conint


class PluginSpec(BaseModel):
    type: str
    params: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


class StrategyDefinition(BaseModel):
    name: str
    symbol: str
    entry: PluginSpec
    exit: PluginSpec
    time_window: PluginSpec
    kill_switch: Optional[PluginSpec] = None
    # 屬於策略定義,不是執行參數(跟 order_qty 被移出去的理由相反)——
    # direction 決定了 entry/exit 該配哪一種 plugin 才有意義(long 配
    # ShortDeviationFromReferenceEntry 沒道理),不是單純「怎麼下單」的
    # 選擇,見 docs/ARCHITECTURE.md §6.11。
    direction: Literal["long", "short"] = "long"
    # 重複次數:總 event 數 = loop + 1,做完 bot 就收尾結束;0 = 只做 1 個 event;
    # null = 不限次數(做到時間窗結束)。event = 部位從 0 開始、回到 0 結束。
    loop: Optional[conint(ge=0)] = 0
    # 分注策略(engine/scale_in_runner.py)= true;必須跟 entry/exit plugin 一致,
    # dsl/loader.py 會檢查。每個策略 YAML 都明確寫出來,一眼看得出是哪一種。
    scale_in: bool = False
    # 預估持倉時間(分注策略):{value: 4, unit: hours};持倉超過就 🟡 警告,不自動平倉;
    # null = 不預估。格式由 engine/hold_time.parse_expected_hold 檢查(2026-10-09)。
    expected_hold: Optional[Dict[str, Any]] = None

    class Config:
        extra = "forbid"
