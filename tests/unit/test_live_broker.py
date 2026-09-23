"""live/broker.py 的邏輯測試——驗證 LiveBroker 怎麼把 interfaces.Broker
這個通用合約(price/qty,不知道 symbol/side)翻譯成 BybitClient 需要的
呼叫(symbol + side + reduce_only)。用一個假的 BybitClient(不是真的
BybitClient,是手寫的 stub),不需要任何真實 API key。

本檔案前半段(TestPlaceLimitBuy 到 TestSatisfiesBrokerProtocol)測的是
`dry_run=False`(真的呼叫 client)時的翻譯邏輯,全部明確傳
`dry_run=False`——因為 `dry_run` 預設值是安全的 `True`,不這樣傳的話
這些測試會全部改成測到 dry-run 行為,不是原本要測的翻譯邏輯。
`TestDryRun` 開始才是 `dry_run=True` 的行為測試。"""

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


def make_broker(client=None, dry_run=False) -> LiveBroker:
    return LiveBroker(client=client or FakeBybitClient(), symbol="BTCUSDT", dry_run=dry_run)


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


class TestDryRunDefault:
    def test_dry_run_defaults_to_true(self):
        # 建構子預設值本身要是安全的——跟 BybitClient.testnet 預設 True
        # 同一種取捨,要不要真的下單,是呼叫端明確傳參數的責任。
        import inspect

        sig = inspect.signature(LiveBroker.__init__)
        assert sig.parameters["dry_run"].default is True


class TestDryRunPlaceOrders:
    def test_place_limit_buy_does_not_call_real_client(self):
        client = FakeBybitClient()
        broker = make_broker(client, dry_run=True)

        broker.place_limit_buy(price=60000.0, qty=0.01)

        assert client.calls == []

    def test_place_limit_buy_returns_open_order_with_synthetic_id(self):
        broker = make_broker(dry_run=True)

        result = broker.place_limit_buy(price=60000.0, qty=0.01)

        assert result.status == "open"
        assert result.price == 60000.0
        assert result.id.startswith("dry-run-")

    def test_place_limit_sell_does_not_call_real_client(self):
        client = FakeBybitClient()
        broker = make_broker(client, dry_run=True)

        broker.place_limit_sell(price=61000.0, qty=0.01)

        assert client.calls == []

    def test_synthetic_order_ids_are_unique(self):
        broker = make_broker(dry_run=True)

        a = broker.place_limit_buy(price=60000.0, qty=0.01)
        b = broker.place_limit_buy(price=60000.0, qty=0.01)

        assert a.id != b.id


class TestDryRunFetchOrder:
    def test_dry_run_order_fills_on_first_fetch_order_call(self):
        """呼應 sat_strategy/app/bot.py 的假設:「dry-run 模式沒有真的
        交易所可以查,直接視為立即成交,方便測試整體流程」——第一次
        查詢就從 open 變成 closed,不用真的等待任何事情發生。"""
        broker = make_broker(dry_run=True)
        placed = broker.place_limit_buy(price=60000.0, qty=0.01)

        fetched = broker.fetch_order(placed.id)

        assert fetched.status == "closed"
        assert fetched.filled_qty == 0.01
        assert fetched.price == 60000.0

    def test_does_not_call_real_client(self):
        client = FakeBybitClient()
        broker = make_broker(client, dry_run=True)
        placed = broker.place_limit_buy(price=60000.0, qty=0.01)

        broker.fetch_order(placed.id)

        assert client.calls == []

    def test_fetching_an_already_filled_dry_run_order_stays_closed(self):
        broker = make_broker(dry_run=True)
        placed = broker.place_limit_buy(price=60000.0, qty=0.01)
        broker.fetch_order(placed.id)  # 第一次查詢,變成 closed

        second_fetch = broker.fetch_order(placed.id)  # 第二次查詢

        assert second_fetch.status == "closed"


class TestDryRunPositionQty:
    def test_increases_after_buy_order_is_polled_filled(self):
        broker = make_broker(dry_run=True)
        placed = broker.place_limit_buy(price=60000.0, qty=0.01)
        assert broker.position_qty() == 0.0  # 還沒被查詢過,還沒「成交」

        broker.fetch_order(placed.id)

        assert broker.position_qty() == 0.01

    def test_decreases_after_sell_order_is_polled_filled(self):
        broker = make_broker(dry_run=True)
        entry = broker.place_limit_buy(price=60000.0, qty=0.01)
        broker.fetch_order(entry.id)
        assert broker.position_qty() == 0.01

        exit_order = broker.place_limit_sell(price=61000.0, qty=0.01)
        broker.fetch_order(exit_order.id)

        assert broker.position_qty() == 0.0

    def test_does_not_call_real_client(self):
        client = FakeBybitClient()
        broker = make_broker(client, dry_run=True)

        broker.position_qty()

        assert client.calls == []


class TestDryRunCancelOrder:
    def test_cancels_open_order_without_calling_client(self):
        client = FakeBybitClient()
        broker = make_broker(client, dry_run=True)
        placed = broker.place_limit_buy(price=60000.0, qty=0.01)

        broker.cancel_order(placed.id)

        assert client.calls == []
        assert broker.fetch_order(placed.id).status == "canceled"

    def test_canceling_an_already_filled_order_is_a_noop(self):
        broker = make_broker(dry_run=True)
        placed = broker.place_limit_buy(price=60000.0, qty=0.01)
        broker.fetch_order(placed.id)  # 先讓它「成交」

        broker.cancel_order(placed.id)  # 不應該把已成交的單改成 canceled

        assert broker.fetch_order(placed.id).status == "closed"


class TestDryRunMarketClose:
    def test_reduces_position_without_calling_client(self):
        client = FakeBybitClient()
        broker = make_broker(client, dry_run=True)
        entry = broker.place_limit_buy(price=60000.0, qty=0.02)
        broker.fetch_order(entry.id)
        assert broker.position_qty() == 0.02

        broker.market_close(0.02)

        assert broker.position_qty() == 0.0
        assert client.calls == []

    def test_never_goes_negative(self):
        broker = make_broker(dry_run=True)

        broker.market_close(0.05)  # 沒有部位,還是被要求平倉

        assert broker.position_qty() == 0.0
