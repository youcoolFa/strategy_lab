"""
live/instrument_limits.py

Bybit 每個交易對都有自己的價格/數量精度限制(priceFilter.tickSize、
lotSizeFilter.qtyStep 等)——不照這個精度下單,交易所會直接拒單。這裡把
下載回來的限制轉成好用的 dataclass,並提供修正 qty/price 到合法精度的
函式。

下載機制(live/fetch_instrument_limits.py)刻意跟這裡的資料模型分開:
抓資料需要真的打 Bybit API(即使是公開、不需要驗證的端點),這個檔案
本身完全不碰網路、不碰檔案 I/O 以外的東西,可以獨立測試。

`instrument_limits.json` committed 進 git 是刻意的(不是密鑰,是公開
市場資料)——這樣測試/沙盒不需要真的連網路就能跑;實際要上線前建議
重新跑一次 fetch_instrument_limits.py,確保精度沒有過期(交易所偶爾
會調整)。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_LIMITS_PATH = Path(__file__).resolve().parents[2] / "instrument_limits.json"


@dataclass
class InstrumentLimits:
    qty_step: float
    min_qty: float
    max_qty: float
    max_market_qty: float
    tick_size: float
    min_price: float
    max_price: float
    min_notional: float


class UnknownSymbolError(Exception):
    """instrument_limits.json 裡沒有這個 symbol 的資料——要嘛還沒下載
    過,要嘛打錯 symbol,不會用預設值矇混過去。"""


def load_instrument_limits(symbol: str, path: Path = DEFAULT_LIMITS_PATH) -> InstrumentLimits:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if symbol not in data or not data[symbol]:
        raise UnknownSymbolError(f"{path} 裡沒有 {symbol} 的資料,先跑 fetch_instrument_limits.py 下載")

    raw = data[symbol]
    price_filter = raw["priceFilter"]
    lot_filter = raw["lotSizeFilter"]
    return InstrumentLimits(
        qty_step=float(lot_filter["qtyStep"]),
        min_qty=float(lot_filter["minOrderQty"]),
        max_qty=float(lot_filter["maxOrderQty"]),
        max_market_qty=float(lot_filter["maxMktOrderQty"]),
        tick_size=float(price_filter["tickSize"]),
        min_price=float(price_filter["minPrice"]),
        max_price=float(price_filter["maxPrice"]),
        min_notional=float(lot_filter["minNotionalValue"]),
    )


def _round_to_step(value: float, step: float, direction: Literal["down", "up"]) -> float:
    """浮點數直接除/乘容易因為浮點誤差多一點點或少一點點(經典的
    0.1 + 0.2 != 0.3 問題)——先用 round() 修掉這個誤差,再乘回 step,
    確保結果真的是 step 的整數倍,不會因為浮點誤差被拒單。"""
    steps = round(value / step, 8)
    steps = math.floor(steps) if direction == "down" else math.ceil(steps)
    return round(steps * step, 8)


def fix_qty(qty: float, limits: InstrumentLimits, order_type: Literal["limit", "market"] = "limit") -> float:
    """永遠只無條件捨去(往下取整)到合法的 qty_step 倍數——不會往上
    調整超過 position_sizing 原本算好的量,寧可少下一點,不多冒風險。
    market 單另外受 max_market_qty 限制(比一般的 max_qty 小,是 Bybit
    真實資料裡兩個不同的欄位)。"""
    rounded = _round_to_step(qty, limits.qty_step, direction="down")
    max_qty = limits.max_market_qty if order_type == "market" else limits.max_qty
    rounded = min(rounded, max_qty)
    if rounded < limits.min_qty:
        raise ValueError(
            f"qty={qty} 修正到 {limits.qty_step} 精度後只剩 {rounded},"
            f"小於交易所允許的最小下單量 min_qty={limits.min_qty}——不會硬湊到最小值,"
            "因為那樣會偷偷改變 position_sizing 原本算好的風險大小"
        )
    return rounded


def fix_price(price: float, limits: InstrumentLimits, side: Literal["Buy", "Sell"]) -> float:
    """限價單價格修正方向依 side 決定,永遠往「對自己保守」的方向修:
    買單(Buy)價格往下修(不會不小心多付錢),賣單(Sell)價格往上修
    (不會不小心少賣錢)。"""
    direction = "down" if side == "Buy" else "up"
    rounded = _round_to_step(price, limits.tick_size, direction=direction)
    return min(max(rounded, limits.min_price), limits.max_price)


def check_min_notional(qty: float, price: float, limits: InstrumentLimits) -> None:
    """市價單沒有呼叫端指定的價格,要自己傳一個當下的參考價(例如
    get_last_price() 查到的)進來檢查——這裡不碰網路。"""
    notional = qty * price
    if notional < limits.min_notional:
        raise ValueError(
            f"qty={qty} x price={price} = {notional:.4f},"
            f"小於交易所允許的最小下單金額 min_notional={limits.min_notional}"
        )
