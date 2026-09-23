"""跟 tests/unit/test_live_bybit_client.py 的差別:unit test 每個方法各自
獨立驗證單一次呼叫;這裡用一個有狀態的假 Bybit 伺服器(模擬訂單從下單
到成交/取消的完整生命週期,類似 broker/paper_broker.py 的角色),驗證
BybitClient 的多個方法接在一起、跨多次呼叫時行為是否正確——例如
「下單後查詢是 open,交易所端成交後再查詢要變成 closed」這種真實使用
情境下的狀態轉移。"""

import pytest

from _stateful_fake_bybit import StatefulFakeBybitServer

from strategy_lab.live.bybit_client import BybitClient


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
        client.place_market_order("BTCUSDT", "Sell", remaining, reduce_only=True)
        # 市價單在(假)交易所是立即成交的,不像限價單需要另外呼叫
        # fill_order() 模擬撮合。

        assert client.get_position_qty("BTCUSDT") == 0.0
