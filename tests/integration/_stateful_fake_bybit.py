"""共用的 integration test 假 Bybit V5 伺服器——不是測試檔本身(檔名故意
加底線前綴,pytest 不會把它當測試模組收集),給
tests/integration/test_live_bybit_client_integration.py 跟
tests/integration/test_live_broker_integration.py 共用。

記住每張單目前的狀態,提供「外部把某張單改成 Filled/Cancelled」的方法,
模擬交易所端的狀態變化跟 BybitClient/LiveBroker 的輪詢是非同步發生的
——角色類似 broker/paper_broker.py 的 PaperBroker,只是這裡故意不會
「餵價格自動成交」(呼應真實交易所自己在背景撮合,不需要外部餵價格)。
"""

import itertools

from pybit.exceptions import InvalidRequestError


class StatefulFakeBybitServer:
    def __init__(self):
        self._orders: dict = {}
        self._id_counter = itertools.count(1)
        self._position_size = 0.0

    def fill_order(self, order_id: str, filled_qty: float, avg_price: float = None) -> None:
        order = self._orders[order_id]
        order["orderStatus"] = "Filled"
        order["cumExecQty"] = str(filled_qty)
        order["avgPrice"] = str(avg_price if avg_price is not None else order["price"])
        self._position_size += filled_qty if order["side"] == "Buy" else -filled_qty

    def cancel_order_externally(self, order_id: str) -> None:
        self._orders[order_id]["orderStatus"] = "Cancelled"

    # ------------------------------------------------------------------
    # pybit.unified_trading.HTTP 介面(只做 BybitClient 用得到的部分)
    # ------------------------------------------------------------------
    def place_order(self, **kwargs):
        order_id = str(next(self._id_counter))
        # 市價單在真實交易所是立即成交的,不像限價單要等外部呼叫
        # fill_order() 模擬撮合——這裡照實模擬,不然 market_close() 這種
        # 用市價單平倉的呼叫,效果不會反映在部位上。
        is_market = kwargs.get("orderType") == "Market"
        self._orders[order_id] = {
            "orderId": order_id,
            "orderStatus": "New",
            "cumExecQty": "0",
            "side": kwargs["side"],
            "price": kwargs.get("price", "0"),
            "avgPrice": "0",
        }
        if is_market:
            self.fill_order(order_id, filled_qty=float(kwargs["qty"]))
        return {"result": {"orderId": order_id}}

    def get_open_orders(self, **kwargs):
        order = self._orders.get(kwargs["orderId"])
        if order and order["orderStatus"] in ("New", "PartiallyFilled"):
            return {"result": {"list": [dict(order)]}}
        return {"result": {"list": []}}

    def get_order_history(self, **kwargs):
        order = self._orders.get(kwargs["orderId"])
        if order and order["orderStatus"] in ("Filled", "Cancelled"):
            return {"result": {"list": [dict(order)]}}
        return {"result": {"list": []}}

    def cancel_order(self, **kwargs):
        order = self._orders.get(kwargs["orderId"])
        if order is None or order["orderStatus"] not in ("New", "PartiallyFilled"):
            raise InvalidRequestError(
                request="cancel_order", message="Order does not exist", status_code=110001, time="t", resp_headers=None
            )
        order["orderStatus"] = "Cancelled"
        return {"result": {}}

    def get_positions(self, **kwargs):
        return {"result": {"list": [{"size": str(self._position_size)}]}}
