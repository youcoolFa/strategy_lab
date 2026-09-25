"""
YAML 策略檔的 schema(pydantic v1)。只負責「這個 dict 合不合法」——
把 `type` 字串解析成實際的 plugin class,是 dsl/loader.py 的職責,這裡
完全不 import registry,也不知道有哪些 plugin 真的存在。

`extra = "forbid"`(對 PluginSpec 跟 StrategyDefinition 都是)是刻意的:
YAML 打錯欄位名這種手誤,要在讀檔的當下就報錯,不能悄悄被忽略。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


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

    class Config:
        extra = "forbid"
