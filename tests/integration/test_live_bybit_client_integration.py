"""跟 tests/unit/test_live_bybit_client.py 的差別:unit test 每個方法各自
獨立驗證單一次呼叫;這裡用一個有狀態的假 Bybit 伺服器(模擬訂單從下單
到成交/取消的完整生命週期,類似 broker/paper_broker.py 的角色),驗證
BybitClient 的多個方法接在一起、跨多次呼叫時行為是否正確——例如
「下單後查詢是 open,交易所端成交後再查詢要變成 closed」這種真實使用
情境下的狀態轉移。"""

import itertools

import pytest
from pybit.exceptions import InvalidRequestError

from strategy_lab.live.bybit_client import BybitClient


class StatefulFakeBybitServer:
    """簡化版的假 Bybit V5 伺服器:記住每張單目前的狀態,提供
    「外部把某張單改成 Filled/Cancelled」的方法,模擬交易所端的狀態
    變化跟 BybitClient 的輪詢是非同步發生的。"""

    def __init__(self):
        self._orders: dict = {}
        self._id_counter = itertools.count(1)
        self._position_size = 0.0

    def fill_order(self, order_id: str, filled_qty: float) -> None:
        self._orders[order_id]["orderStatus"] = "Filled"
        self._orders[order_id]["cumExecQty"] = str(filled_qty)
        side = self._orders[order_id]["side"]
        self._position_size += filled_qty if side == "Buy" else -filled_qty

    def cancel_order_externally(self, order_id: str) -> None:
        self._orders[order_id]["orderStatus"] = "Cancelled"

    # ------------------------------------------------------------------
    # pybit.unified_trading.HTTP 介面(只做 BybitClient 用得到的部分)
    # ------------------------------------------------------------------
    def place_order(self, **kwargs):
        order_id = str(next(self._id_counter))
        self._orders[order_id] = {
            "orderId": order_id,
            "orderStatus": "New",
            "cumExecQty": "0",
            "side": kwargs["side"],
        }
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


@pytest.fixture
def server():
    return StatefulFakeBybitServer()


@pytest.fixture
def client(server):
    return BybitClient(api_key="test", api_secret="test", http_client=server)


class TestFullOrderLifecycle:
    def test_place_then_poll_open_then_fill_then_poll_closed(self, client, server):
        order = client.place_limit_order("BTCUSDT", "Buy", 0.01, 60000.0)
        assert client.get_order_status("BTCUSDT", order.order_id).status == "open"

        server.fill_order(order.order_id, filled_qty=0.01)

        status = client.get_order_status("BTCUSDT", order.order_id)
        assert status.status == "closed"
        assert status.filled_qty == 0.01
        assert client.get_position_qty("BTCUSDT") == 0.01

    def test_entry_fill_then_exit_fill_returns_position_to_flat(self, client, server):
        entry = client.place_limit_order("BTCUSDT", "Buy", 0.01, 60000.0)
        server.fill_order(entry.order_id, filled_qty=0.01)
        assert client.get_position_qty("BTCUSDT") == 0.01

        exit_order = client.place_limit_order("BTCUSDT", "Sell", 0.01, 61000.0, reduce_only=True)
        server.fill_order(exit_order.order_id, filled_qty=0.01)
        assert client.get_position_qty("BTCUSDT") == 0.0

    def test_cancel_open_order_then_status_reflects_canceled(self, client, server):
        order = client.place_limit_order("BTCUSDT", "Buy", 0.01, 60000.0)
        client.cancel_order("BTCUSDT", order.order_id)

        status = client.get_order_status("BTCUSDT", order.order_id)
        assert status.status == "canceled"

    def test_canceling_an_already_filled_order_does_not_raise(self, client, server):
        """對應 bot.py 的 _cancel_order():cleanup 階段可能對一張其實
        已經成交的單再送一次取消(輪詢跟實際成交有時間差),交易所會說
        「訂單不存在」,呼叫端要把這種情況當成功處理,不能整個流程炸掉。"""
        order = client.place_limit_order("BTCUSDT", "Buy", 0.01, 60000.0)
        server.fill_order(order.order_id, filled_qty=0.01)

        client.cancel_order("BTCUSDT", order.order_id)  # 不應該 raise

    def test_market_order_for_cleanup_closes_remaining_position(self, client, server):
        entry = client.place_limit_order("BTCUSDT", "Buy", 0.02, 60000.0)
        server.fill_order(entry.order_id, filled_qty=0.02)
        assert client.get_position_qty("BTCUSDT") == 0.02

        remaining = client.get_position_qty("BTCUSDT")
        market_exit = client.place_market_order("BTCUSDT", "Sell", remaining, reduce_only=True)
        server.fill_order(market_exit.order_id, filled_qty=remaining)

        assert client.get_position_qty("BTCUSDT") == 0.0
