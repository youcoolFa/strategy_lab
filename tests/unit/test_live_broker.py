"""live/broker.py 的邏輯測試——驗證 LiveBroker 怎麼把 interfaces.Broker
這個通用合約(price/qty,不知道 symbol/side)翻譯成 BybitClient 需要的
呼叫(symbol + side + reduce_only)。用一個假的 BybitClient(不是真的
BybitClient,是手寫的 stub),不需要任何真實 API key。"""

from strategy_lab.interfaces import Broker, OrderLike
from strategy_lab.live.broker import LiveBroker, LiveOrder
from strategy_lab.live.bybit_client import OrderResult


class FakeBybitClient:
    """手寫的假 BybitClient——記錄每次呼叫的參數,回傳預先設定好的結果。"""

    def __init__(self):
        self.calls = []
        self.place_limit_order_result = None
        self.place_market_order_result = None
        self.get_order_status_result = None
        self.position_qty_result = 0.0

    def place_limit_order(self, symbol, side, qty, price, reduce_only=False):
        self.calls.append(("place_limit_order", symbol, side, qty, price, reduce_only))
        return self.place_limit_order_result

    def place_market_order(self, symbol, side, qty, reduce_only=False):
        self.calls.append(("place_market_order", symbol, side, qty, reduce_only))
        return self.place_market_order_result

    def get_order_status(self, symbol, order_id):
        self.calls.append(("get_order_status", symbol, order_id))
        return self.get_order_status_result

    def cancel_order(self, symbol, order_id):
        self.calls.append(("cancel_order", symbol, order_id))

    def get_position_qty(self, symbol):
        self.calls.append(("get_position_qty", symbol))
        return self.position_qty_result


def make_broker(client=None) -> LiveBroker:
    return LiveBroker(client=client or FakeBybitClient(), symbol="BTCUSDT")


class TestPlaceLimitBuy:
    def test_translates_to_buy_side_not_reduce_only(self):
        client = FakeBybitClient()
        client.place_limit_order_result = OrderResult(order_id="o1", status="open", price=60000.0)
        broker = make_broker(client)

        result = broker.place_limit_buy(price=60000.0, qty=0.01)

        assert client.calls == [("place_limit_order", "BTCUSDT", "Buy", 0.01, 60000.0, False)]
        assert result == LiveOrder(id="o1", status="open", price=60000.0, filled_qty=0.0)


class TestPlaceLimitSell:
    def test_translates_to_sell_side_reduce_only(self):
        """exit 永遠是平倉,reduce_only 一定是 True——不能因為呼叫端忘記
        傳而意外開出新倉。"""
        client = FakeBybitClient()
        client.place_limit_order_result = OrderResult(order_id="o2", status="open", price=61000.0)
        broker = make_broker(client)

        broker.place_limit_sell(price=61000.0, qty=0.01)

        assert client.calls == [("place_limit_order", "BTCUSDT", "Sell", 0.01, 61000.0, True)]


class TestFetchOrder:
    def test_returns_order_like_result(self):
        client = FakeBybitClient()
        client.get_order_status_result = OrderResult(order_id="o1", status="closed", price=60000.0, filled_qty=0.01)
        broker = make_broker(client)

        result = broker.fetch_order("o1")

        assert client.calls == [("get_order_status", "BTCUSDT", "o1")]
        assert result == LiveOrder(id="o1", status="closed", price=60000.0, filled_qty=0.01)


class TestCancelOrder:
    def test_forwards_symbol_and_order_id(self):
        client = FakeBybitClient()
        broker = make_broker(client)

        broker.cancel_order("o1")

        assert client.calls == [("cancel_order", "BTCUSDT", "o1")]


class TestPositionQty:
    def test_forwards_to_client(self):
        client = FakeBybitClient()
        client.position_qty_result = 0.03
        broker = make_broker(client)

        assert broker.position_qty() == 0.03
        assert client.calls == [("get_position_qty", "BTCUSDT")]


class TestMarketClose:
    def test_places_a_reduce_only_market_sell(self):
        """整個系統只做多(entry=買、exit=賣),市價平倉一定是賣出。"""
        client = FakeBybitClient()
        client.place_market_order_result = OrderResult(order_id="o3", status="open")
        broker = make_broker(client)

        broker.market_close(0.02)

        assert client.calls == [("place_market_order", "BTCUSDT", "Sell", 0.02, True)]


class TestTickIsANoOp:
    def test_does_not_call_the_client_at_all(self):
        """真實交易所自己在背景撮合,不需要外部餵價格才成交——這跟
        PaperBroker.tick() 的語意完全不同,但兩者都滿足同一個 Broker
        Protocol,runner.py 不需要知道差異。"""
        client = FakeBybitClient()
        broker = make_broker(client)

        broker.tick(60000.0)

        assert client.calls == []


class TestSatisfiesBrokerProtocol:
    def test_live_broker_is_a_broker(self):
        assert isinstance(make_broker(), Broker)

    def test_live_order_is_order_like(self):
        assert isinstance(LiveOrder(id="1", status="open", price=1.0, filled_qty=0.0), OrderLike)
