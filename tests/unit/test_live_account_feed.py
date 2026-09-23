"""live/account_feed.py 的純邏輯測試——只測不需要真實/假 Redis 連線的
部分:`_handle_entry()` 怎麼更新內部狀態、`get_latest_*()` 怎麼讀。
需要真的驅動 Redis Streams 讀取迴圈的部分,見
tests/integration/test_live_account_feed_integration.py。"""

import json

from strategy_lab.live.account_feed import ORDER_STREAM, POSITION_STREAM, WALLET_STREAM, AccountFeed


def make_entry(payload: dict) -> dict:
    return {"data": json.dumps(payload)}


class TestGetLatestWithNoData:
    def test_get_latest_wallet_returns_none(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        assert feed.get_latest_wallet("USDT") is None

    def test_get_latest_position_returns_none(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        assert feed.get_latest_position("USDT") is None

    def test_get_latest_order_returns_none(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        assert feed.get_latest_order() is None


class TestHandleWalletEntry:
    def test_stores_by_coin(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        feed._handle_entry(WALLET_STREAM, make_entry({"coin": "USDT", "equity": "100.5"}))
        assert feed.get_latest_wallet("USDT") == {"coin": "USDT", "equity": "100.5"}

    def test_different_coins_kept_independently(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        feed._handle_entry(WALLET_STREAM, make_entry({"coin": "USDT", "equity": "100"}))
        feed._handle_entry(WALLET_STREAM, make_entry({"coin": "BTC", "equity": "0.01"}))
        assert feed.get_latest_wallet("USDT")["equity"] == "100"
        assert feed.get_latest_wallet("BTC")["equity"] == "0.01"

    def test_same_coin_overwritten_by_newer_entry(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        feed._handle_entry(WALLET_STREAM, make_entry({"coin": "USDT", "equity": "100"}))
        feed._handle_entry(WALLET_STREAM, make_entry({"coin": "USDT", "equity": "150"}))
        assert feed.get_latest_wallet("USDT")["equity"] == "150"

    def test_missing_coin_field_is_ignored(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        feed._handle_entry(WALLET_STREAM, make_entry({"equity": "100"}))
        assert feed.get_latest_wallet("USDT") is None

    def test_returned_dict_is_a_copy_not_internal_reference(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        feed._handle_entry(WALLET_STREAM, make_entry({"coin": "USDT", "equity": "100"}))
        snapshot = feed.get_latest_wallet("USDT")
        snapshot["equity"] = "TAMPERED"
        assert feed.get_latest_wallet("USDT")["equity"] == "100"


class TestHandlePositionEntry:
    def test_stores_by_settle_coin(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        feed._handle_entry(POSITION_STREAM, make_entry({"settle_coin": "USDT", "size": "0.01"}))
        assert feed.get_latest_position("USDT") == {"settle_coin": "USDT", "size": "0.01"}

    def test_missing_settle_coin_field_is_ignored(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        feed._handle_entry(POSITION_STREAM, make_entry({"size": "0.01"}))
        assert feed.get_latest_position("USDT") is None


class TestHandleOrderEntry:
    def test_stores_latest_order(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        feed._handle_entry(ORDER_STREAM, make_entry({"order_id": "1", "order_status": "New"}))
        assert feed.get_latest_order() == {"order_id": "1", "order_status": "New"}

    def test_only_keeps_most_recent_order_regardless_of_id(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        feed._handle_entry(ORDER_STREAM, make_entry({"order_id": "1", "order_status": "New"}))
        feed._handle_entry(ORDER_STREAM, make_entry({"order_id": "2", "order_status": "New"}))
        assert feed.get_latest_order()["order_id"] == "2"


class TestHandleMalformedEntry:
    def test_invalid_json_is_ignored_without_raising(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        feed._handle_entry(WALLET_STREAM, {"data": "not valid json {{{"})
        assert feed.get_latest_wallet("USDT") is None

    def test_missing_data_key_is_ignored_without_raising(self):
        feed = AccountFeed(redis_url="redis://irrelevant/0")
        feed._handle_entry(WALLET_STREAM, {})
        assert feed.get_latest_wallet("USDT") is None


class TestStartWithoutRedisUrl:
    def test_does_not_spawn_thread(self):
        feed = AccountFeed(redis_url=None)
        feed.start()
        assert feed._thread is None
