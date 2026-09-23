"""
live/market_feed.py

從 sat_strategy/app/market_feed.py 移植過來,行為完全一致,唯一差異是
把訊息解析從 `_subscribe_loop()` 迴圈裡抽成獨立的 `_handle_message()`
方法(原版直接寫在迴圈裡)——這樣解析邏輯可以獨立單元測試,不用真的
跑一次 Pub/Sub 訂閱迴圈。

訂閱 Fa_Successful_trade(獨立專案,負責接收/解析 Bybit WS 資料)透過
Redis 廣播出來的即時行情事件。

Fa_Successful_trade 那邊的分工(見 app/market_data/event_publisher.py):
    - ticker(高頻、公開行情)  -> Redis Pub/Sub,channel "bybit:ticker"
    - wallet/position/order(低頻、帳戶私有資料)-> Redis Streams,
      stream "bybit:wallet" / "bybit:position" / "bybit:order"

這裡只實作 TickerFeed(Pub/Sub):
    ticker 是公開市場資料,不管哪個 Bybit 帳戶都是同一份,接進來沒有風險。
    wallet/position/order 是「哪個帳戶」的資料,見 live/account_feed.py。

用同步 redis client(不是 redis.asyncio):跟 sat_strategy 的取捨一致,
避免整個執行層需要重寫成 asyncio event loop。
"""

import json
import os
import threading
from typing import Optional

import redis
from loguru import logger

TICKER_CHANNEL = "bybit:ticker"


class TickerFeed:
    """
    背景 thread 訂閱 Redis Pub/Sub 的 bybit:ticker,把收到的最新價格存進
    thread-safe 的變數,供主迴圈呼叫 get_last_price() 讀取。

    連不上 Redis、或還沒收到任何訊息時,get_last_price() 回傳 None——呼叫
    端要自己決定 fallback(不要讓邏輯完全依賴這個 feed 是否活著)。
    """

    def __init__(self, redis_url: Optional[str] = None):
        self._redis_url = redis_url or os.getenv("REDIS_URL")
        self._lock = threading.Lock()
        self._last_price: Optional[float] = None
        self._thread = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if not self._redis_url:
            logger.warning("[TickerFeed] REDIS_URL 未設定,不會啟動即時行情訂閱。")
            return

        self._thread = threading.Thread(target=self._run, daemon=True, name="ticker-feed")
        self._thread.start()
        logger.info(f"[TickerFeed] 已啟動,訂閱 Redis channel: {TICKER_CHANNEL}")

    def stop(self) -> None:
        self._stop_event.set()

    def get_last_price(self) -> Optional[float]:
        with self._lock:
            return self._last_price

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._subscribe_loop()
            except redis.RedisError as e:
                logger.warning(f"[TickerFeed] Redis 連線發生問題,5 秒後重試:{e}")
                self._stop_event.wait(5)

    def _subscribe_loop(self) -> None:
        client = redis.Redis.from_url(self._redis_url)
        pubsub = client.pubsub()
        pubsub.subscribe(TICKER_CHANNEL)

        for message in pubsub.listen():
            if self._stop_event.is_set():
                break
            self._handle_message(message)

    def _handle_message(self, message: dict) -> None:
        if message["type"] != "message":
            return

        try:
            data = json.loads(message["data"])
            price = float(data["last_price"])
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"[TickerFeed] 收到的訊息格式不對,略過:{e}")
            return

        with self._lock:
            self._last_price = price


# 全域共用的 instance,跟 live/account_feed.py 的用法一致:呼叫端直接
# import 這個 instance 使用,不用自己管生命週期。
ticker_feed = TickerFeed()
