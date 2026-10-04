"""
live/bybit_client.py

包裝 pybit(Bybit V5 原生 Python SDK),取代 sat_strategy/app/bot.py
原本用的 ccxt。方法對應 bot.py 原本呼叫的 ccxt 方法,一對一改寫:

    ccxt (sat_strategy)              -> BybitClient(strategy_lab)
    fetch_ticker                     -> get_last_price
    create_limit_buy/sell_order      -> place_limit_order
    create_market_sell_order         -> place_market_order
    fetch_open_order/fetch_closed_order -> get_order_status
    cancel_order                     -> cancel_order
    fetch_positions                  -> get_position_qty

不直接在建構子裡寫死 `pybit.unified_trading.HTTP(...)`,而是透過
`http_client` 參數注入——測試時可以塞一個假的 client 進來,不需要真的
呼叫 Bybit API、不需要任何真實 API key。真正串接真實帳戶(需要真實
key/secret)是下一階段的事,這個檔案本身只需要 http_client 的介面
形狀正確就能測試完整。

`category` 是建構子參數,預設 `"linear"`(USDT 永續合約,對齊
sat_strategy 原本的假設)——2026-09-26 改成可設定,因為使用者實際部署
時想拿掉「只能交易永續合約」這個隱性假設,讓 `live_execution_config.yaml`
自己決定要用 `spot`/`linear`/`inverse`/`option` 哪一種 Bybit V5 商品
類型。

重試邏輯對應 bot.py 的 `_call_with_retry`:只重試 `FailedRequestError`
(網路層失敗,通常是暫時性的);`InvalidRequestError`(Bybit 回傳明確
的業務錯誤,例如餘額不足)不重試,直接往上拋——重試一個確定會再次失敗
的請求沒有意義。
"""

from __future__ import annotations

import os
from datetime import datetime
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from pybit.exceptions import FailedRequestError, InvalidRequestError
from pybit.unified_trading import HTTP

_ORDER_NOT_FOUND_PHRASES = ("order does not exist", "order not exists", "order not found")


class BybitAPIError(Exception):
    """呼叫端不需要認識 pybit 底層的例外型別,統一包裝成這個。"""


@dataclass
class OrderResult:
    order_id: str
    status: str  # "open" | "closed" | "canceled"
    price: float = 0.0
    filled_qty: float = 0.0


_OPEN_ORDER_STATUSES = {"Created", "New", "PartiallyFilled", "Untriggered", "Triggered", "Active"}


def _to_order_result(order: dict) -> OrderResult:
    order_status = order.get("orderStatus", "New")
    if order_status in _OPEN_ORDER_STATUSES:
        return OrderResult(
            order_id=order["orderId"],
            status="open",
            price=float(order.get("price") or 0.0),
            filled_qty=float(order["cumExecQty"]),
        )
    status = "closed" if order_status == "Filled" else "canceled"
    # 優先用 avgPrice(實際成交均價);avgPrice 是 "0"(沒成交就被取消)才退回用 price。
    price = float(order.get("avgPrice") or 0.0) or float(order.get("price") or 0.0)
    return OrderResult(order_id=order["orderId"], status=status, price=price, filled_qty=float(order["cumExecQty"]))


class BybitClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = True,
        max_retries: int = 5,
        retry_backoff_cap_seconds: float = 30.0,
        category: str = "linear",
        http_client: Optional[HTTP] = None,
    ) -> None:
        self._max_retries = max_retries
        self._retry_backoff_cap_seconds = retry_backoff_cap_seconds
        self._category = category
        self._http = http_client or HTTP(
            testnet=testnet,
            api_key=api_key or os.getenv("BYBIT_API_KEY", ""),
            api_secret=api_secret or os.getenv("BYBIT_API_SECRET", ""),
        )

    def _call_with_retry(self, func, **kwargs) -> dict:
        for attempt in range(self._max_retries):
            try:
                return func(**kwargs)
            except FailedRequestError:
                if attempt == self._max_retries - 1:
                    raise
                wait = min(2**attempt, self._retry_backoff_cap_seconds)
                time.sleep(wait)

    def get_last_price(self, symbol: str) -> float:
        resp = self._call_with_retry(self._http.get_tickers, category=self._category, symbol=symbol)
        return float(resp["result"]["list"][0]["lastPrice"])

    def place_limit_order(self, symbol: str, side: str, qty: float, price: float, reduce_only: bool = False) -> OrderResult:
        resp = self._call_with_retry(
            self._http.place_order,
            category=self._category,
            symbol=symbol,
            side=side,
            orderType="Limit",
            qty=str(qty),
            price=str(price),
            reduceOnly=reduce_only,
            timeInForce="GTC",
        )
        return OrderResult(order_id=resp["result"]["orderId"], status="open", price=price)

    def place_market_order(self, symbol: str, side: str, qty: float, reduce_only: bool = False) -> OrderResult:
        resp = self._call_with_retry(
            self._http.place_order,
            category=self._category,
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            reduceOnly=reduce_only,
        )
        return OrderResult(order_id=resp["result"]["orderId"], status="open")

    def get_order_status(self, symbol: str, order_id: str) -> OrderResult:
        # 帶 orderId 查 /v5/order/realtime 時,剛成交/取消的單也會回傳,不能
        # 看到就當 open,一定要看 orderStatus(2026-09-27 真實 mainnet 踩到:
        # 買單已成交卻一直被當成未成交,平倉單從未掛出)。
        open_resp = self._call_with_retry(self._http.get_open_orders, category=self._category, symbol=symbol, orderId=order_id)
        open_list = open_resp["result"]["list"]
        if open_list:
            return _to_order_result(open_list[0])

        history_resp = self._call_with_retry(
            self._http.get_order_history, category=self._category, symbol=symbol, orderId=order_id
        )
        history_list = history_resp["result"]["list"]
        if not history_list:
            raise BybitAPIError(f"訂單 {order_id} 在未成交跟歷史清單都找不到")
        return _to_order_result(history_list[0])

    def cancel_order(self, symbol: str, order_id: str) -> None:
        try:
            self._call_with_retry(self._http.cancel_order, category=self._category, symbol=symbol, orderId=order_id)
        except InvalidRequestError as e:
            if not any(phrase in str(e).lower() for phrase in _ORDER_NOT_FOUND_PHRASES):
                raise
            # 訂單已經不存在(已成交/已取消)——視為成功,呼叫端不用分辨這種差異。

    def get_position_qty(self, symbol: str) -> float:
        """多單正數、空單負數。Bybit 的 size 永遠是正數,方向在 side
        ("Buy"/"Sell",沒持倉時是空字串)。"""
        resp = self._call_with_retry(self._http.get_positions, category=self._category, symbol=symbol)
        positions = resp["result"]["list"]
        total = 0.0
        for p in positions:
            size = float(p.get("size") or 0.0)
            total += -size if p.get("side") == "Sell" else size
        return total

    def get_fee_rates(self, symbol: str) -> Tuple[float, float]:
        """(maker, taker) 手續費率,這個帳戶在這個交易對的真實費率。"""
        resp = self._call_with_retry(self._http.get_fee_rates, category=self._category, symbol=symbol)
        row = resp["result"]["list"][0]
        return float(row["makerFeeRate"]), float(row["takerFeeRate"])

    def get_leverage(self, symbol: str) -> Optional[float]:
        resp = self._call_with_retry(self._http.get_positions, category=self._category, symbol=symbol)
        positions = resp["result"]["list"]
        return float(positions[0]["leverage"]) if positions and positions[0].get("leverage") else None

    def get_margin_mode(self) -> Optional[str]:
        resp = self._call_with_retry(self._http.get_account_info)
        return resp["result"].get("marginMode")

    def get_executions(self, symbol: str, start: datetime, end: datetime) -> list:
        """成交明細(含 execType=Funding 的資金費),以交易所回報的手續費為準。
        Bybit 一次最多回 100 筆,用 nextPageCursor 翻頁;查詢區間上限 7 天。"""
        params = dict(
            category=self._category, symbol=symbol, limit=100,
            startTime=int(start.timestamp() * 1000), endTime=int(end.timestamp() * 1000),
        )
        rows: list = []
        while True:
            resp = self._call_with_retry(self._http.get_executions, **params)
            rows.extend(resp["result"]["list"])
            cursor = resp["result"].get("nextPageCursor")
            if not cursor:
                return rows
            params["cursor"] = cursor

    def get_open_orders(self, symbol: str) -> list:
        resp = self._call_with_retry(self._http.get_open_orders, category=self._category, symbol=symbol)
        return resp["result"]["list"]

    def get_instrument_info(self, symbol: str) -> dict:
        """回傳單一交易對的原始 instrument 資料(priceFilter/lotSizeFilter
        這些)——公開端點,不需要驗證。解析成好用的形狀是
        live/instrument_limits.py 的職責,這裡刻意只當一層薄薄的包裝。"""
        resp = self._call_with_retry(self._http.get_instruments_info, category=self._category, symbol=symbol)
        return resp["result"]["list"][0]

    def get_account_equity(self) -> float:
        """回傳 UNIFIED 帳戶的總權益(USD 計價)——`position_sizing.mode
        == "account_percentage"` 用這個數字換算 qty。這是需要驗證的
        端點(要真實 API key),不像 get_last_price()/get_instrument_info()
        是公開的。帳戶沒有任何資產時 Bybit 可能回傳空清單,回傳 0.0 而
        不是丟例外,讓呼叫端自己決定要不要當成錯誤處理。"""
        resp = self._call_with_retry(self._http.get_wallet_balance, accountType="UNIFIED")
        accounts = resp["result"]["list"]
        if not accounts:
            return 0.0
        return float(accounts[0]["totalEquity"])
