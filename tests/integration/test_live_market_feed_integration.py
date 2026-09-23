"""用 fakeredis 真的跑一次 TickerFeed 的背景 thread + Pub/Sub 訂閱迴圈。

跟 test_live_account_feed_integration.py 的時序取捨不同:Pub/Sub 沒有
歷史回放,訂閱前發布的訊息永遠收不到,所以這裡用「先啟動、確認訂閱生效、
再發布」——fakeredis 的 pubsub().listen() 在背景 thread 裡會正確阻塞、
新訊息到達時正確喚醒(已驗證跟 XREADGROUP 的 block 參數行為不同)。"""

import json
import time

import fakeredis
import pytest

from strategy_lab.live.market_feed import TickerFeed


@pytest.fixture
def fake_redis_url():
    return f"redis://fake-market-feed-{time.time_ns()}/0"


def make_client(url: str) -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis.from_url(url)


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
        "strategy_lab.live.market_feed.redis.Redis.from_url",
        staticmethod(lambda url: make_client(url)),
    )


class TestTickerFeedAgainstFakeRedis:
    def test_receives_price_published_after_start(self, patched_redis, fake_redis_url):
        feed = TickerFeed(redis_url=fake_redis_url)
        feed.start()
        try:
            # 給背景 thread 時間完成 subscribe()——Pub/Sub 沒有回放,
            # 訂閱生效前發布的訊息永遠收不到。
            time.sleep(0.3)
            producer = make_client(fake_redis_url)
            producer.publish("bybit:ticker", json.dumps({"last_price": "60123.45"}).encode())

            assert wait_until(lambda: feed.get_last_price() is not None)
            assert feed.get_last_price() == 60123.45
        finally:
            feed.stop()

    def test_later_price_overwrites_earlier_one(self, patched_redis, fake_redis_url):
        feed = TickerFeed(redis_url=fake_redis_url)
        feed.start()
        try:
            time.sleep(0.3)
            producer = make_client(fake_redis_url)
            producer.publish("bybit:ticker", json.dumps({"last_price": "100"}).encode())
            assert wait_until(lambda: feed.get_last_price() == 100.0)

            producer.publish("bybit:ticker", json.dumps({"last_price": "200"}).encode())
            assert wait_until(lambda: feed.get_last_price() == 200.0)
        finally:
            feed.stop()

    def test_malformed_message_does_not_crash_the_subscriber_thread(self, patched_redis, fake_redis_url):
        feed = TickerFeed(redis_url=fake_redis_url)
        feed.start()
        try:
            time.sleep(0.3)
            producer = make_client(fake_redis_url)
            producer.publish("bybit:ticker", b"not valid json {{{")
            producer.publish("bybit:ticker", json.dumps({"last_price": "77.7"}).encode())

            assert wait_until(lambda: feed.get_last_price() == 77.7)
            assert feed._thread.is_alive()
        finally:
            feed.stop()
