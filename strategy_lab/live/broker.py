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

`tick(price)` 在真實模式下沒有作用:真實交易所自己在背景撮合訂單,
不需要外部餵價格才成交。dry-run 模式下它只記下最新價,給下面的模擬
成交判斷用。`runner.py` 不需要知道這個差異。

`dry_run`(預設 `True`,安全預設)對應 sat_strategy/app/bot.py 每個下單
方法前的 `if self.config.dry_run: return ...`。放在 `LiveBroker` 而不是
`BybitClient`:`BybitClient` 保持忠實、無條件包裝真實 API(即使在模擬
模式下,可能還是想用它查真實價格);「要不要真的下單」的決策屬於
`LiveBroker`,它才是 bot.py `_place_entry_order`/`_place_exit_order`
這些方法的對應位置。

dry-run 訂單在 `fetch_order()` 查詢時才判斷成交:**市價單**第一次查詢
就成交;**限價單**要等 `tick()` 看到的價格碰到限價(買單 價格 <= 限價、
賣單 價格 >= 限價)才成交,跟 `PaperBroker` 同一條規則。bot.py 的
dry-run 是「查詢一次就當成交」,但搭配一啟動就掛單的機制,會變成每個
輪詢週期都假成交一輪(sat_strategy 8/1 的 dry-run log 就是這樣跑出
7.7 萬次循環),模擬結果沒有參考價值——見 docs/ARCHITECTURE.md §6.14。

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
    order_type: str = "limit"


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
    _dry_run_last_price: Optional[float] = field(default=None, init=False)
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
            return self._dry_run_place("Buy", self.client.get_last_price(self.symbol), qty, reduce_only=False, order_type="market")
        result = self.client.place_market_order(self.symbol, "Buy", qty, reduce_only=False)
        return _to_live_order(result)

    def market_sell(self, qty: float) -> LiveOrder:
        qty = self._fix_market_qty(qty)
        if self.dry_run:
            return self._dry_run_place("Sell", self.client.get_last_price(self.symbol), qty, reduce_only=False, order_type="market")
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
            return self._dry_run_place("Sell", self.client.get_last_price(self.symbol), qty, reduce_only=True, order_type="market")
        result = self.client.place_market_order(self.symbol, "Sell", qty, reduce_only=True)
        return _to_live_order(result)

    def market_flat_sell(self, qty: float) -> LiveOrder:
        qty = self._fix_market_qty(qty)
        if self.dry_run:
            return self._dry_run_place("Buy", self.client.get_last_price(self.symbol), qty, reduce_only=True, order_type="market")
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
        # 真實模式下交易所自己撮合,這裡什麼都不用做;dry-run 記下最新價,
        # 讓限價單要等價格碰到限價才模擬成交(跟 PaperBroker.tick() 一致)。
        self._dry_run_last_price = price

    # ------------------------------------------------------------------
    # dry-run 內部模擬
    # ------------------------------------------------------------------
    def _dry_run_place(
        self, side: str, price: float, qty: float, reduce_only: bool, order_type: str = "limit"
    ) -> LiveOrder:
        order_id = f"dry-run-{next(self._dry_run_id_counter)}"
        order = LiveOrder(
            id=order_id, status="open", price=price, side=side, qty=qty, reduce_only=reduce_only, order_type=order_type
        )
        self._dry_run_orders[order_id] = order
        logger.info(
            f"[dry-run] 模擬下 {order_type} {side} 單:{self.symbol} @ {price} qty={qty:.4f} reduce_only={reduce_only}"
        )
        return order

    def _dry_run_price_reached(self, order: LiveOrder) -> bool:
        if order.order_type == "market":
            return True
        last = self._dry_run_last_price
        if last is None:
            return False
        return last <= order.price if order.side == "Buy" else last >= order.price

    def _dry_run_fill_if_pending(self, order_id: str) -> LiveOrder:
        order = self._dry_run_orders[order_id]
        if order.status == "open" and self._dry_run_price_reached(order):
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
