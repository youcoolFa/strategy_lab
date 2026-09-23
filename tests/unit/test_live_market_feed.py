"""live/market_feed.py 的純邏輯測試——只測不需要真實/假 Redis 連線的
部分。需要真的驅動 Pub/Sub 訂閱迴圈的部分,見
tests/integration/test_live_market_feed_integration.py。"""

from strategy_lab.live.market_feed import TickerFeed


class TestGetLastPriceWithNoData:
    def test_returns_none_before_any_message_received(self):
        feed = TickerFeed(redis_url="redis://irrelevant/0")
        assert feed.get_last_price() is None


class TestStartWithoutRedisUrl:
    def test_does_not_spawn_thread(self):
        feed = TickerFeed(redis_url=None)
        feed.start()
        assert feed._thread is None


class TestLastPriceStateIsThreadSafe:
    def test_set_then_get_roundtrip(self):
        feed = TickerFeed(redis_url="redis://irrelevant/0")
        with feed._lock:
            feed._last_price = 60123.45
        assert feed.get_last_price() == 60123.45


class TestHandleMessage:
    """對應 account_feed.py 的 _handle_entry():把訊息解析抽出來獨立
    可測試,不用真的跑一次 Pub/Sub 訂閱迴圈。"""

    def test_updates_last_price_on_valid_message(self):
        feed = TickerFeed(redis_url="redis://irrelevant/0")
        feed._handle_message({"type": "message", "data": '{"last_price": "60123.45"}'})
        assert feed.get_last_price() == 60123.45

    def test_ignores_non_message_types(self):
        feed = TickerFeed(redis_url="redis://irrelevant/0")
        feed._handle_message({"type": "subscribe", "data": 1})
        assert feed.get_last_price() is None

    def test_invalid_json_is_ignored_without_raising(self):
        feed = TickerFeed(redis_url="redis://irrelevant/0")
        feed._handle_message({"type": "message", "data": "not valid json {{{"})
        assert feed.get_last_price() is None

    def test_missing_last_price_field_is_ignored_without_raising(self):
        feed = TickerFeed(redis_url="redis://irrelevant/0")
        feed._handle_message({"type": "message", "data": '{"symbol": "BTCUSDT"}'})
        assert feed.get_last_price() is None

    def test_later_valid_message_overwrites_earlier_price(self):
        feed = TickerFeed(redis_url="redis://irrelevant/0")
        feed._handle_message({"type": "message", "data": '{"last_price": "100"}'})
        feed._handle_message({"type": "message", "data": '{"last_price": "200"}'})
        assert feed.get_last_price() == 200.0
