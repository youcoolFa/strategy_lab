"""
live/broker.py

`LiveBroker` 把 `BybitClient`(需要 symbol/side、知道自己是哪個交易對)
包裝成滿足 `interfaces.Broker` 這個通用合約(只認 price/qty,不知道
symbol 是什麼)的物件——跟 `broker/paper_broker.py` 的 `PaperBroker`
是同一種角色,一個是真實版,一個是模擬版,`engine/runner.py` 兩者都能
接,不需要知道背後是哪一種。

`symbol` 存在 `LiveBroker` 自己身上(建構時決定,整個 runner 的生命
週期不會變),不是每次呼叫都要傳——這是 `Broker` 合約的方法簽名
(`place_limit_buy(price, qty)`)天生就沒有 symbol 參數的原因。

`tick(price)` 是刻意的 no-op:真實交易所自己在背景撮合訂單,不需要
外部餵價格才成交——這跟 `PaperBroker.tick()` 的語意完全不同,但兩者
都滿足同一個 `Broker` Protocol,`runner.py` 不需要、也不應該知道這個
差異。

`dry_run`(預設 `True`,安全預設)對應 sat_strategy/app/bot.py 每個下單
方法前的 `if self.config.dry_run: return ...`。放在 `LiveBroker` 而不是
`BybitClient`:`BybitClient` 保持忠實、無條件包裝真實 API(即使在模擬
模式下,可能還是想用它查真實價格);「要不要真的下單」的決策屬於
`LiveBroker`,它才是 bot.py `_place_entry_order`/`_place_exit_order`
這些方法的對應位置。

dry-run 訂單在**第一次被 `fetch_order()` 查詢時**才從 open 變成
closed(不是下單當下就立即成交)——呼應 bot.py 的假設:「dry-run 模式
沒有真的交易所可以查,直接視為立即成交,方便測試整體流程」,那個假設
在 bot.py 是在輪詢迴圈裡兌現的,這裡搬到 `fetch_order()` 兌現,行為
等價:runner.py 下單後一定會在下一次 tick 呼叫 `fetch_order()` 才會
知道有沒有成交,所以「下單當下」跟「第一次查詢時」對 runner.py 來說
沒有可觀察的差異。市價單 dry-run 也沿用這個模式(不特地「立刻成交」)。

**市價單 dry-run 下單當下會呼叫 `client.get_last_price()` 查一次真實
市價,當作模擬成交價**——限價單不會(呼叫端已經算好價格傳進來了)。
這是唯讀查詢,不是下單,`dry_run=True` 只擋「會真的花錢的動作」,不擋
查價——跟 `BybitClient` 模組docstring「即使在模擬模式下,可能還是想用
它查真實價格」是同一個取捨。沒有這一步,dry-run 市價單的
`active_entry_price` 會是沒有意義的 `0.0`,跑起來的 log 沒辦法用來
初步檢視策略邏輯合不合理。

8 種下單方式(方向 buy/sell × 單種類 limit/market × 開倉/平倉),對稱於
broker/paper_broker.py 的 PaperBroker——見 docs/ARCHITECTURE.md §6.8:
    place_limit_buy   開多倉,限價,reduce_only=False
    limit_sell        開空倉,限價,reduce_only=False
    market_buy        開多倉,市價,reduce_only=False
    market_sell       開空倉,市價,reduce_only=False
    place_limit_sell  平多倉,限價,reduce_only=True(= limit_flat_buy)
    limit_flat_sell   平空倉,限價,reduce_only=True
    market_flat_buy   平多倉,市價,reduce_only=True
    market_flat_sell  平空倉,市價,reduce_only=True

`reduce_only=True` 的 dry-run 單一樣不會讓模擬部位「穿越」0——跟
PaperBroker._fill() 同一條規則,不然沙盒/dry-run 測出來的部位軌跡會
跟真實環境對不上。

**每一種下單方式,不管 dry-run 還是真的下單,送出去之前都會先用
`instrument_limits.py` 修正 qty/price 的精度**(`_fix_limit_order()`/
`_fix_market_qty()`)——Bybit 每個交易對都有自己的 tickSize/qtyStep,
`position_sizing` 算出來的浮點數(例如 0.003333...)幾乎一定對不上,不
修正直接送出去,交易所會直接拒單。找不到這個 symbol 的精度資料(還沒
下載過/symbol 打錯)時不會讓下單整個掛掉,原樣傳給 client,交由交易所
自己的驗證把關——見 docs/ARCHITECTURE.md §6.9。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

from loguru import logger

from strategy_lab.live.bybit_client import BybitClient, OrderResult
from strategy_lab.live.instrument_limits import (
    DEFAULT_LIMITS_PATH,
    InstrumentLimits,
    UnknownSymbolError,
    fix_price,
    fix_qty,
    load_instrument_limits,
)


@dataclass
class LiveOrder:
    id: str
    status: str  # "open" | "closed" | "canceled"
    price: float
    side: Optional[str] = None
    qty: float = 0.0
    filled_qty: float = 0.0
    reduce_only: bool = False


def _to_live_order(result: OrderResult) -> LiveOrder:
    return LiveOrder(id=result.order_id, status=result.status, price=result.price, filled_qty=result.filled_qty)


@dataclass
class LiveBroker:
    client: BybitClient
    symbol: str
    dry_run: bool = True
    instrument_limits_path: Path = field(default_factory=lambda: DEFAULT_LIMITS_PATH)

    _dry_run_orders: Dict[str, LiveOrder] = field(default_factory=dict, init=False)
    _dry_run_position_qty: float = field(default=0.0, init=False)
    _dry_run_id_counter: itertools.count = field(default_factory=lambda: itertools.count(1), init=False)
    _limits: Optional[InstrumentLimits] = field(default=None, init=False, repr=False)
    _limits_loaded: bool = field(default=False, init=False, repr=False)

    def _get_limits(self) -> Optional[InstrumentLimits]:
        if not self._limits_loaded:
            self._limits_loaded = True
            try:
                self._limits = load_instrument_limits(self.symbol, path=self.instrument_limits_path)
            except (FileNotFoundError, UnknownSymbolError) as e:
                logger.warning(f"[instrument_limits] 找不到 {self.symbol} 的精度限制,略過自動修正:{e}")
        return self._limits

    def _fix_limit_order(self, price: float, qty: float, side: str) -> Tuple[float, float]:
        limits = self._get_limits()
        if limits is None:
            return price, qty
        return fix_price(price, limits, side=side), fix_qty(qty, limits, order_type="limit")

    def _fix_market_qty(self, qty: float) -> float:
        limits = self._get_limits()
        if limits is None:
            return qty
        return fix_qty(qty, limits, order_type="market")

    # --- 開倉 ---
    def place_limit_buy(self, price: float, qty: float) -> LiveOrder:
        price, qty = self._fix_limit_order(price, qty, side="Buy")
        if self.dry_run:
            return self._dry_run_place("Buy", price, qty, reduce_only=False)
        result = self.client.place_limit_order(self.symbol, "Buy", qty, price, reduce_only=False)
        return _to_live_order(result)

    def limit_sell(self, price: float, qty: float) -> LiveOrder:
        price, qty = self._fix_limit_order(price, qty, side="Sell")
        if self.dry_run:
            return self._dry_run_place("Sell", price, qty, reduce_only=False)
        result = self.client.place_limit_order(self.symbol, "Sell", qty, price, reduce_only=False)
        return _to_live_order(result)

    def market_buy(self, qty: float) -> LiveOrder:
        qty = self._fix_market_qty(qty)
        if self.dry_run:
            return self._dry_run_place("Buy", self.client.get_last_price(self.symbol), qty, reduce_only=False)
        result = self.client.place_market_order(self.symbol, "Buy", qty, reduce_only=False)
        return _to_live_order(result)

    def market_sell(self, qty: float) -> LiveOrder:
        qty = self._fix_market_qty(qty)
        if self.dry_run:
            return self._dry_run_place("Sell", self.client.get_last_price(self.symbol), qty, reduce_only=False)
        result = self.client.place_market_order(self.symbol, "Sell", qty, reduce_only=False)
        return _to_live_order(result)

    # --- 平倉 ---
    def place_limit_sell(self, price: float, qty: float) -> LiveOrder:
        # exit 永遠是平倉,reduce_only 寫死 True——不能因為呼叫端忘記
        # 傳而意外開出新倉。
        price, qty = self._fix_limit_order(price, qty, side="Sell")
        if self.dry_run:
            return self._dry_run_place("Sell", price, qty, reduce_only=True)
        result = self.client.place_limit_order(self.symbol, "Sell", qty, price, reduce_only=True)
        return _to_live_order(result)

    def limit_flat_buy(self, price: float, qty: float) -> LiveOrder:
        """跟 place_limit_sell 是同一件事——見 PaperBroker 同名方法的
        說明,保留 place_limit_sell 是因為 runner.py/既有測試已經在用
        這個名字。"""
        return self.place_limit_sell(price, qty)

    def limit_flat_sell(self, price: float, qty: float) -> LiveOrder:
        price, qty = self._fix_limit_order(price, qty, side="Buy")
        if self.dry_run:
            return self._dry_run_place("Buy", price, qty, reduce_only=True)
        result = self.client.place_limit_order(self.symbol, "Buy", qty, price, reduce_only=True)
        return _to_live_order(result)

    def market_flat_buy(self, qty: float) -> LiveOrder:
        qty = self._fix_market_qty(qty)
        if self.dry_run:
            return self._dry_run_place("Sell", self.client.get_last_price(self.symbol), qty, reduce_only=True)
        result = self.client.place_market_order(self.symbol, "Sell", qty, reduce_only=True)
        return _to_live_order(result)

    def market_flat_sell(self, qty: float) -> LiveOrder:
        qty = self._fix_market_qty(qty)
        if self.dry_run:
            return self._dry_run_place("Buy", self.client.get_last_price(self.symbol), qty, reduce_only=True)
        result = self.client.place_market_order(self.symbol, "Buy", qty, reduce_only=True)
        return _to_live_order(result)

    def fetch_order(self, order_id: str) -> LiveOrder:
        if order_id in self._dry_run_orders:
            return self._dry_run_fill_if_pending(order_id)
        result = self.client.get_order_status(self.symbol, order_id)
        return _to_live_order(result)

    def cancel_order(self, order_id: str) -> None:
        if order_id in self._dry_run_orders:
            order = self._dry_run_orders[order_id]
            if order.status == "open":
                logger.info(f"[dry-run] 模擬取消訂單 {order_id}")
                order.status = "canceled"
            return
        self.client.cancel_order(self.symbol, order_id)

    def position_qty(self) -> float:
        if self.dry_run:
            return self._dry_run_position_qty
        return self.client.get_position_qty(self.symbol)

    def market_close(self, qty: float) -> None:
        # 整個系統只做多(entry=買、exit=賣),市價平倉一定是賣出。
        if self.dry_run:
            logger.info(f"[dry-run] 模擬市價平倉 {self.symbol} qty={qty:.4f}")
            self._dry_run_position_qty = max(0.0, self._dry_run_position_qty - qty)
            return
        self.client.place_market_order(self.symbol, "Sell", qty, reduce_only=True)

    def tick(self, price: float) -> None:
        pass

    # ------------------------------------------------------------------
    # dry-run 內部模擬
    # ------------------------------------------------------------------
    def _dry_run_place(self, side: str, price: float, qty: float, reduce_only: bool) -> LiveOrder:
        order_id = f"dry-run-{next(self._dry_run_id_counter)}"
        order = LiveOrder(id=order_id, status="open", price=price, side=side, qty=qty, reduce_only=reduce_only)
        self._dry_run_orders[order_id] = order
        logger.info(f"[dry-run] 模擬下 {side} 單:{self.symbol} @ {price:.2f} qty={qty:.4f} reduce_only={reduce_only}")
        return order

    def _dry_run_fill_if_pending(self, order_id: str) -> LiveOrder:
        order = self._dry_run_orders[order_id]
        if order.status == "open":
            order.status = "closed"
            order.filled_qty = order.qty
            delta = order.qty if order.side == "Buy" else -order.qty
            new_qty = self._dry_run_position_qty + delta
            if order.reduce_only:
                # 平倉單不該讓部位穿越 0——跟 PaperBroker._fill() 同一條規則。
                if self._dry_run_position_qty > 0:
                    new_qty = max(0.0, new_qty)
                elif self._dry_run_position_qty < 0:
                    new_qty = min(0.0, new_qty)
            self._dry_run_position_qty = new_qty
        return order
