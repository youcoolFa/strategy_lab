"""broker/paper_broker.py:8 種下單方式(方向 buy/sell × 單種類
limit/market × 開倉/平倉),對稱於 live/broker.py 的 LiveBroker——見
docs/ARCHITECTURE.md §6.8。之前只有 place_limit_buy/place_limit_sell/
market_close 三個,間接透過 runner 整合測試驗證,沒有專屬的單元測試檔,
這次順便補齊。"""

from strategy_lab.broker.paper_broker import PaperBroker


class TestLimitOrdersOpenPosition:
    def test_place_limit_buy_opens_long_when_price_crosses(self):
        broker = PaperBroker()
        order = broker.place_limit_buy(price=100.0, qty=1.0)
        assert order.status == "open"
        broker.tick(100.0)
        assert broker.fetch_order(order.id).status == "closed"
        assert broker.position_qty() == 1.0

    def test_limit_sell_opens_short_when_price_crosses(self):
        """開空倉:跟 place_limit_sell(平多倉,reduce_only)不同,這個
        允許部位變成負數。"""
        broker = PaperBroker()
        order = broker.limit_sell(price=100.0, qty=1.0)
        broker.tick(100.0)
        assert broker.fetch_order(order.id).status == "closed"
        assert broker.position_qty() == -1.0


class TestMarketOrdersOpenPosition:
    def test_market_buy_fills_immediately_at_last_ticked_price(self):
        broker = PaperBroker()
        broker.tick(100.0)  # 先讓 broker 知道目前價格
        order = broker.market_buy(qty=1.0)
        assert order.status == "closed"  # 市價單立刻成交,不用等下一次 tick 穿越
        assert order.price == 100.0
        assert broker.position_qty() == 1.0

    def test_market_sell_opens_short_immediately(self):
        broker = PaperBroker()
        broker.tick(100.0)
        order = broker.market_sell(qty=1.0)
        assert order.status == "closed"
        assert broker.position_qty() == -1.0


class TestFlatOrdersClosePosition:
    def test_limit_flat_buy_closes_long_and_is_equivalent_to_place_limit_sell(self):
        broker = PaperBroker()
        broker.place_limit_buy(price=100.0, qty=1.0)
        broker.tick(100.0)
        assert broker.position_qty() == 1.0

        order = broker.limit_flat_buy(price=110.0, qty=1.0)
        broker.tick(110.0)
        assert broker.fetch_order(order.id).status == "closed"
        assert broker.position_qty() == 0.0

    def test_limit_flat_sell_closes_short(self):
        broker = PaperBroker()
        broker.limit_sell(price=100.0, qty=1.0)
        broker.tick(100.0)
        assert broker.position_qty() == -1.0

        order = broker.limit_flat_sell(price=90.0, qty=1.0)
        broker.tick(90.0)
        assert broker.fetch_order(order.id).status == "closed"
        assert broker.position_qty() == 0.0

    def test_market_flat_buy_closes_long_immediately(self):
        broker = PaperBroker()
        broker.place_limit_buy(price=100.0, qty=1.0)
        broker.tick(100.0)
        assert broker.position_qty() == 1.0

        order = broker.market_flat_buy(qty=1.0)
        assert order.status == "closed"
        assert broker.position_qty() == 0.0

    def test_market_flat_sell_closes_short_immediately(self):
        broker = PaperBroker()
        broker.limit_sell(price=100.0, qty=1.0)
        broker.tick(100.0)
        assert broker.position_qty() == -1.0

        order = broker.market_flat_sell(qty=1.0)
        assert order.status == "closed"
        assert broker.position_qty() == 0.0


class TestFlatOrdersCannotFlipPositionSide:
    """reduce_only 語意:平倉單只能把部位收斂到 0,不能倒過來開出反方向
    的新倉——這是真實交易所 reduceOnly 參數的行為,PaperBroker 要模擬
    一致,不然沙盒測出來的部位軌跡會跟真實環境對不上。"""

    def test_limit_flat_buy_qty_larger_than_position_clamps_at_zero(self):
        broker = PaperBroker()
        broker.place_limit_buy(price=100.0, qty=1.0)
        broker.tick(100.0)

        broker.limit_flat_buy(price=100.0, qty=5.0)  # 部位只有 1.0,卻想平 5.0
        broker.tick(100.0)

        assert broker.position_qty() == 0.0  # 不會變成 -4.0

    def test_market_flat_sell_qty_larger_than_short_position_clamps_at_zero(self):
        broker = PaperBroker()
        broker.limit_sell(price=100.0, qty=1.0)
        broker.tick(100.0)

        broker.market_flat_sell(qty=5.0)

        assert broker.position_qty() == 0.0  # 不會變成 +4.0


class TestMarketCloseStillWorksUnchanged:
    """market_close() 是 _cleanup() 用的既有方法,不屬於 8 種命名方式的
    一部分,行為必須完全不變。"""

    def test_market_close_reduces_long_position_to_zero(self):
        broker = PaperBroker()
        broker.place_limit_buy(price=100.0, qty=1.0)
        broker.tick(100.0)

        broker.market_close(1.0)

        assert broker.position_qty() == 0.0
