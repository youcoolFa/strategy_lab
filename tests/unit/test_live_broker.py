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
        self.last_price_result = 0.0

    def place_limit_order(self, symbol, side, qty, price, reduce_only=False):
        self.calls.append(("place_limit_order", symbol, side, qty, price, reduce_only))
        return self.place_limit_order_result

    def place_market_order(self, symbol, side, qty, reduce_only=False):
        self.calls.append(("place_market_order", symbol, side, qty, reduce_only))
        return self.place_market_order_result

    def get_last_price(self, symbol):
        self.calls.append(("get_last_price", symbol))
        return self.last_price_result

    def get_order_status(self, symbol, order_id):
        self.calls.append(("get_order_status", symbol, order_id))
        return self.get_order_status_result

    def cancel_order(self, symbol, order_id):
        self.calls.append(("cancel_order", symbol, order_id))

    def get_position_qty(self, symbol):
        self.calls.append(("get_position_qty", symbol))
        return self.position_qty_result


def make_broker(client=None, dry_run=False, instrument_limits_path=None) -> LiveBroker:
    kwargs = {}
    if instrument_limits_path is not None:
        kwargs["instrument_limits_path"] = instrument_limits_path
    return LiveBroker(client=client or FakeBybitClient(), symbol="BTCUSDT", dry_run=dry_run, **kwargs)


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


class TestEightOrderMethodsTranslateCorrectly:
    """8 種下單方式(方向 buy/sell × 單種類 limit/market × 開倉/平倉),
    對稱於 broker/paper_broker.py——見 docs/ARCHITECTURE.md §6.8。
    place_limit_buy/place_limit_sell 已經在上面兩個 class 測過,這裡補
    剩下 6 個 + limit_flat_buy 別名。"""

    def test_limit_sell_opens_short_not_reduce_only(self):
        client = FakeBybitClient()
        client.place_limit_order_result = OrderResult(order_id="o", status="open", price=60000.0)
        broker = make_broker(client)

        broker.limit_sell(price=60000.0, qty=0.01)

        assert client.calls == [("place_limit_order", "BTCUSDT", "Sell", 0.01, 60000.0, False)]

    def test_market_buy_opens_long_not_reduce_only(self):
        client = FakeBybitClient()
        client.place_market_order_result = OrderResult(order_id="o", status="open")
        broker = make_broker(client)

        broker.market_buy(qty=0.01)

        assert client.calls == [("place_market_order", "BTCUSDT", "Buy", 0.01, False)]

    def test_market_sell_opens_short_not_reduce_only(self):
        client = FakeBybitClient()
        client.place_market_order_result = OrderResult(order_id="o", status="open")
        broker = make_broker(client)

        broker.market_sell(qty=0.01)

        assert client.calls == [("place_market_order", "BTCUSDT", "Sell", 0.01, False)]

    def test_limit_flat_buy_is_equivalent_to_place_limit_sell(self):
        client = FakeBybitClient()
        client.place_limit_order_result = OrderResult(order_id="o", status="open", price=61000.0)
        broker = make_broker(client)

        broker.limit_flat_buy(price=61000.0, qty=0.01)

        assert client.calls == [("place_limit_order", "BTCUSDT", "Sell", 0.01, 61000.0, True)]

    def test_limit_flat_sell_closes_short_reduce_only(self):
        client = FakeBybitClient()
        client.place_limit_order_result = OrderResult(order_id="o", status="open", price=59000.0)
        broker = make_broker(client)

        broker.limit_flat_sell(price=59000.0, qty=0.01)

        assert client.calls == [("place_limit_order", "BTCUSDT", "Buy", 0.01, 59000.0, True)]

    def test_market_flat_buy_closes_long_reduce_only(self):
        client = FakeBybitClient()
        client.place_market_order_result = OrderResult(order_id="o", status="open")
        broker = make_broker(client)

        broker.market_flat_buy(qty=0.01)

        assert client.calls == [("place_market_order", "BTCUSDT", "Sell", 0.01, True)]

    def test_market_flat_sell_closes_short_reduce_only(self):
        client = FakeBybitClient()
        client.place_market_order_result = OrderResult(order_id="o", status="open")
        broker = make_broker(client)

        broker.market_flat_sell(qty=0.01)

        assert client.calls == [("place_market_order", "BTCUSDT", "Buy", 0.01, True)]


class TestInstrumentLimitsAreAppliedBeforeSendingToClient:
    """使用者提出的需求:出單前用 Bybit 的商品精度限制檢查/修正 qty、
    price,通常是浮點數問題——見 live/instrument_limits.py。這裡故意用
    一份 tickSize/qtyStep 明顯的假資料(不是真實下載的那份),讓修正
    前後的差異看得出來,不依賴真實下載檔案裡實際的數字。"""

    def _limits_path(self, tmp_path):
        import json

        path = tmp_path / "instrument_limits.json"
        path.write_text(
            json.dumps(
                {
                    "BTCUSDT": {
                        "priceFilter": {"minPrice": "0.10", "maxPrice": "1999999.80", "tickSize": "0.50"},
                        "lotSizeFilter": {
                            "maxOrderQty": "1500.000",
                            "minOrderQty": "0.001",
                            "qtyStep": "0.010",
                            "maxMktOrderQty": "150.000",
                            "minNotionalValue": "5",
                        },
                    }
                }
            )
        )
        return path

    def test_limit_buy_qty_and_price_are_rounded_before_being_sent(self, tmp_path):
        client = FakeBybitClient()
        client.place_limit_order_result = OrderResult(order_id="o", status="open", price=60000.0)
        broker = make_broker(client, instrument_limits_path=self._limits_path(tmp_path))

        broker.place_limit_buy(price=60000.37, qty=0.0137)

        # qty_step=0.01 -> 0.0137 捨去成 0.01;tickSize=0.5、買單往下修
        # -> 60000.37 修成 60000.0
        assert client.calls == [("place_limit_order", "BTCUSDT", "Buy", 0.01, 60000.0, False)]

    def test_limit_sell_price_rounds_up_not_down(self, tmp_path):
        client = FakeBybitClient()
        client.place_limit_order_result = OrderResult(order_id="o", status="open", price=60000.5)
        broker = make_broker(client, instrument_limits_path=self._limits_path(tmp_path))

        broker.place_limit_sell(price=60000.13, qty=0.02)

        assert client.calls == [("place_limit_order", "BTCUSDT", "Sell", 0.02, 60000.5, True)]

    def test_market_qty_is_rounded_before_being_sent(self, tmp_path):
        client = FakeBybitClient()
        client.place_market_order_result = OrderResult(order_id="o", status="open")
        broker = make_broker(client, instrument_limits_path=self._limits_path(tmp_path))

        broker.market_buy(qty=0.0149)

        assert client.calls == [("place_market_order", "BTCUSDT", "Buy", 0.01, False)]

    def test_dry_run_also_applies_the_fix_so_the_preview_is_accurate(self, tmp_path):
        broker = make_broker(dry_run=True, instrument_limits_path=self._limits_path(tmp_path))

        placed = broker.place_limit_buy(price=60000.37, qty=0.0137)

        assert placed.qty == 0.01
        assert placed.price == 60000.0

    def test_missing_limits_file_degrades_gracefully_without_raising(self, tmp_path):
        """還沒下載過、或這個 symbol 不在檔案裡——不該讓下單整個掛掉,
        原樣傳給 client,交由交易所自己的驗證把關。"""
        client = FakeBybitClient()
        client.place_limit_order_result = OrderResult(order_id="o", status="open", price=60000.37)
        missing_path = tmp_path / "does_not_exist.json"
        broker = make_broker(client, instrument_limits_path=missing_path)

        broker.place_limit_buy(price=60000.37, qty=0.0137)

        assert client.calls == [("place_limit_order", "BTCUSDT", "Buy", 0.0137, 60000.37, False)]


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


class TestDryRunEightOrderMethods:
    def test_market_buy_queries_real_price_but_does_not_place_a_real_order(self):
        """市價單的「價格」本來就沒有使用者能指定的意義,dry-run 要模擬
        得像樣一點,還是得知道真實市價是多少——但只查價(唯讀),不會
        真的下單。跟限價單一樣,第一次 fetch_order() 才變 closed。"""
        client = FakeBybitClient()
        client.last_price_result = 61234.5
        broker = make_broker(client, dry_run=True)

        placed = broker.market_buy(qty=0.01)
        assert client.calls == [("get_last_price", "BTCUSDT")]  # 只查價,沒下單
        assert placed.status == "open"
        assert placed.price == 61234.5

        fetched = broker.fetch_order(placed.id)
        assert fetched.status == "closed"
        assert broker.position_qty() == 0.01

    def test_market_sell_queries_real_price(self):
        client = FakeBybitClient()
        client.last_price_result = 61234.5
        broker = make_broker(client, dry_run=True)

        placed = broker.market_sell(qty=0.01)

        assert client.calls == [("get_last_price", "BTCUSDT")]
        assert placed.price == 61234.5

    def test_market_flat_buy_queries_real_price(self):
        client = FakeBybitClient()
        client.last_price_result = 61234.5
        broker = make_broker(client, dry_run=True)

        placed = broker.market_flat_buy(qty=0.01)

        assert client.calls == [("get_last_price", "BTCUSDT")]
        assert placed.price == 61234.5

    def test_market_flat_sell_queries_real_price(self):
        client = FakeBybitClient()
        client.last_price_result = 58900.0
        broker = make_broker(client, dry_run=True)

        placed = broker.market_flat_sell(qty=0.01)

        assert client.calls == [("get_last_price", "BTCUSDT")]
        assert placed.price == 58900.0

    def test_limit_orders_do_not_query_price_only_market_orders_do(self):
        """限價單的價格是呼叫端算好傳進來的,不該多打一次網路請求去查
        真實市價——只有市價單需要,因為它本來就沒有呼叫端指定的價格。"""
        client = FakeBybitClient()
        broker = make_broker(client, dry_run=True)

        broker.place_limit_buy(price=60000.0, qty=0.01)
        broker.limit_sell(price=60000.0, qty=0.01)

        assert client.calls == []

    def test_limit_sell_opens_short_position(self):
        broker = make_broker(dry_run=True)
        placed = broker.limit_sell(price=60000.0, qty=0.01)
        broker.fetch_order(placed.id)
        assert broker.position_qty() == -0.01

    def test_market_flat_sell_closes_short_position(self):
        broker = make_broker(dry_run=True)
        opened = broker.limit_sell(price=60000.0, qty=0.01)
        broker.fetch_order(opened.id)
        assert broker.position_qty() == -0.01

        closed = broker.market_flat_sell(qty=0.01)
        broker.fetch_order(closed.id)
        assert broker.position_qty() == 0.0

    def test_flat_order_larger_than_position_clamps_at_zero_not_flipped(self):
        """reduce_only 語意:平倉單不該讓部位穿越 0——跟
        broker/paper_broker.py 的 TestFlatOrdersCannotFlipPositionSide
        是同一個規則,LiveBroker 的 dry-run 模擬要一致。"""
        broker = make_broker(dry_run=True)
        opened = broker.place_limit_buy(price=60000.0, qty=1.0)
        broker.fetch_order(opened.id)
        assert broker.position_qty() == 1.0

        closed = broker.market_flat_buy(qty=5.0)  # 部位只有 1.0,卻想平 5.0
        broker.fetch_order(closed.id)

        assert broker.position_qty() == 0.0  # 不會變成 -4.0


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
