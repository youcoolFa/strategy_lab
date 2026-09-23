"""用 fakeredis(記憶體內的 Redis 實作,不需要真的 Redis server)真的跑
一次 AccountFeed 的背景 thread + XREADGROUP 訂閱迴圈,驗證跟真實 Redis
的 wire protocol 互動是正確的——不只是 _handle_entry() 這個單一方法的
邏輯(那已經在 tests/unit/test_live_account_feed.py 測過)。

刻意的設計:訊息在呼叫 feed.start() 之前就已經寫進 stream、consumer
group 也已經先建好——這樣第一次 xreadgroup(">") 呼叫時資料就已經存在,
不需要依賴「背景 thread 正在阻塞、新資料寫入時會被喚醒」這個時序。
fakeredis 的 block 參數不是真的阻塞(呼叫後幾乎立刻回傳空結果,不等新
資料),用「先發訊息再啟動」能避開這個限制,而不是跟它硬碰硬。"""

import json
import time

import fakeredis
import pytest

from strategy_lab.live.account_feed import CONSUMER_GROUP, AccountFeed


@pytest.fixture
def fake_redis_url():
    # 每個測試用不同的 URL,確保各自連到獨立的假伺服器,測試之間不互相干擾。
    return f"redis://fake-account-feed-{time.time_ns()}/0"


def make_client(url: str) -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis.from_url(url, decode_responses=True)


def wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def patched_redis(monkeypatch):
    monkeypatch.setattr(
        "strategy_lab.live.account_feed.redis.Redis.from_url",
        staticmethod(lambda url, decode_responses=True: make_client(url)),
    )


class TestAccountFeedAgainstFakeRedis:
    def test_receives_wallet_update_already_queued_when_started(self, patched_redis, fake_redis_url):
        producer = make_client(fake_redis_url)
        producer.xgroup_create("bybit:wallet", CONSUMER_GROUP, id="$", mkstream=True)
        producer.xadd("bybit:wallet", {"data": json.dumps({"coin": "USDT", "equity": "250.0"})})

        feed = AccountFeed(redis_url=fake_redis_url)
        feed.start()
        try:
            assert wait_until(lambda: feed.get_latest_wallet("USDT") is not None)
            assert feed.get_latest_wallet("USDT")["equity"] == "250.0"
        finally:
            feed.stop()

    def test_receives_position_and_order_updates(self, patched_redis, fake_redis_url):
        producer = make_client(fake_redis_url)
        producer.xgroup_create("bybit:position", CONSUMER_GROUP, id="$", mkstream=True)
        producer.xgroup_create("bybit:order", CONSUMER_GROUP, id="$", mkstream=True)
        producer.xadd("bybit:position", {"data": json.dumps({"settle_coin": "USDT", "size": "0.02"})})
        producer.xadd("bybit:order", {"data": json.dumps({"order_id": "abc123", "order_status": "Filled"})})

        feed = AccountFeed(redis_url=fake_redis_url)
        feed.start()
        try:
            assert wait_until(lambda: feed.get_latest_position("USDT") is not None)
            assert wait_until(lambda: feed.get_latest_order() is not None)
            assert feed.get_latest_position("USDT")["size"] == "0.02"
            assert feed.get_latest_order()["order_id"] == "abc123"
        finally:
            feed.stop()

    def test_message_is_acked_after_being_consumed(self, patched_redis, fake_redis_url):
        """消費完的訊息要被 XACK,不能一直卡在 pending list 裡——不然
        consumer group 的 pending 清單會無限增長。"""
        producer = make_client(fake_redis_url)
        producer.xgroup_create("bybit:wallet", CONSUMER_GROUP, id="$", mkstream=True)
        producer.xadd("bybit:wallet", {"data": json.dumps({"coin": "USDT", "equity": "1.0"})})

        feed = AccountFeed(redis_url=fake_redis_url)
        feed.start()
        try:
            assert wait_until(lambda: feed.get_latest_wallet("USDT") is not None)
            assert wait_until(lambda: producer.xpending("bybit:wallet", CONSUMER_GROUP)["pending"] == 0)
        finally:
            feed.stop()

    def test_new_consumer_group_does_not_see_messages_added_before_it_was_created(
        self, patched_redis, fake_redis_url
    ):
        """group 用 id="$" 建立(只在意「現在」的帳戶狀態),不重播
        Fa_Successful_trade 啟動以來的完整歷史——這是刻意的設計,不是
        遺漏,見 account_feed.py 的 docstring。"""
        producer = make_client(fake_redis_url)
        producer.xadd("bybit:wallet", {"data": json.dumps({"coin": "USDT", "equity": "OLD"})})

        feed = AccountFeed(redis_url=fake_redis_url)
        feed.start()
        try:
            time.sleep(0.3)  # 給背景 thread 足夠時間把「舊訊息」處理過(理應忽略)一輪
            assert feed.get_latest_wallet("USDT") is None
        finally:
            feed.stop()
