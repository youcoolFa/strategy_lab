"""
dsl/order_config.py

「怎麼下單」的資料(symbol/order_type/position_sizing),跟
strategies/*.yaml(「什麼時候該不該觸發」的策略邏輯)刻意分開——策略的
threshold_price/deviation_pct 這些是校準給特定 symbol 用的,不該跟「這次
要下多大」混在一起;而「下多大」又該跟帳戶規模掛鉤,不該寫死在策略檔裡。

sandbox(demo/sandbox_order.yaml,用假設的 account_value)和 live
(live_execution_config.yaml 的 position_sizing 欄位)各自一份設定檔,
共用這裡的 schema 跟換算邏輯,不重複寫兩次。

三種 position_sizing 模式:
  fixed_qty          直接給數量,跟原本 strategies/*.yaml 的 order_qty
                      語意相同,不需要知道價格或帳戶餘額。
  fixed_quote_amount  給報價貨幣金額(如 500 USDT),除以當時價格換算成 qty。
  account_percentage  給帳戶權益的百分比,除了要當時價格,還要知道帳戶
                      權益——sandbox 用 YAML 裡直接寫的假設值
                      (account_value);live 端 BybitClient 目前還沒有
                      查真實餘額的方法,沒給 account_value 就會 raise,
                      不會靜默算出一個危險或錯誤的數字。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import yaml

PositionSizingMode = Literal["fixed_qty", "fixed_quote_amount", "account_percentage"]


@dataclass
class PositionSizing:
    mode: PositionSizingMode = "fixed_qty"
    value: float = 1.0


@dataclass
class OrderConfig:
    symbol: Optional[str] = None  # 不設就用策略 YAML 自己的 symbol
    order_type: Literal["limit", "market"] = "limit"
    position_sizing: PositionSizing = field(default_factory=PositionSizing)
    account_value: Optional[float] = None  # 只有 account_percentage 模式會用到


def load_order_config(path: Path) -> OrderConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    sizing_raw = raw.pop("position_sizing", None)
    config = OrderConfig(**raw)
    if sizing_raw is not None:
        config.position_sizing = PositionSizing(**sizing_raw)
    return config


def compute_qty(order_config: OrderConfig, current_price: float) -> float:
    """把 position_sizing 換算成真正要傳給 broker 的 qty。呼叫端(sandbox
    的 demo/run_from_yaml.py、live 的 live/main.py)自己決定用哪個價格
    (sandbox 用合成 feed 的起始價,live 用真實查到的當前價),這裡不碰
    任何 I/O。"""
    mode = order_config.position_sizing.mode
    value = order_config.position_sizing.value

    if mode == "fixed_qty":
        return value
    if mode == "fixed_quote_amount":
        return value / current_price
    if mode == "account_percentage":
        if order_config.account_value is None:
            raise ValueError(
                "position_sizing.mode 是 account_percentage,但沒有 account_value 可用"
                "(sandbox 請在 order YAML 裡直接給一個假設值;live 端目前還沒有查真實"
                "帳戶餘額的功能,這個模式暫時不能用在真實執行上)"
            )
        return order_config.account_value * (value / 100) / current_price
    raise ValueError(f"未知的 position_sizing.mode: {mode!r}")
