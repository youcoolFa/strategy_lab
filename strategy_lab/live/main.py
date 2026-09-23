"""
live/main.py

真正的執行入口,對應 sat_strategy/app/bot.py 的 main()/SatStrategyBot.run()。

流程:
    1. load_dotenv() 讀 .env(真實 API key、REDIS_URL 這些不進 git 的值)
    2. load_execution_config() 讀執行參數(dry_run/testnet/輪詢間隔...)
    3. dsl.loader.load_strategy() 讀策略(entry/exit/time_window/
       kill_switch,參數都在 YAML 裡,這個檔案不重複定義)
    4. 組出 BybitClient -> LiveBroker -> StrategyRunner
    5. 如果 use_live_ticker_feed,啟動 market_feed.ticker_feed
    6. 用真實時鐘驅動的迴圈跑 runner.tick(),直到 STOPPED
    7. 收到 SIGINT/SIGTERM 呼叫 runner.request_stop(),讓下一次 tick()
       觸發正常的 _cleanup() 收尾,不是直接砍掉 process 留下沒人管的
       真實掛單或部位

執行方式:
    /opt/anaconda3/bin/python3 -m strategy_lab.live.main
"""

from __future__ import annotations

import signal
import time
from datetime import datetime
from typing import Callable, Optional, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from loguru import logger

from strategy_lab.dsl.loader import load_strategy
from strategy_lab.engine.runner import RunState, StrategyRunner
from strategy_lab.live.broker import LiveBroker
from strategy_lab.live.bybit_client import BybitClient
from strategy_lab.live.config import ExecutionConfig, load_execution_config
from strategy_lab.live.market_feed import ticker_feed

HKT = ZoneInfo("Asia/Hong_Kong")


def to_bybit_symbol(yaml_symbol: str) -> str:
    """YAML 裡的 symbol 是給人看的格式(如 "BTC/USDT"、ccxt 風格的
    "BTC/USDT:USDT"),Bybit 原生 API 要的是不帶分隔符的格式(如
    "BTCUSDT")。"""
    return yaml_symbol.split(":")[0].replace("/", "")


def build_runner_and_symbol(config: ExecutionConfig) -> Tuple[StrategyRunner, str]:
    strategy = load_strategy(config.strategy_path)
    symbol = config.symbol_override or to_bybit_symbol(strategy.symbol)

    bybit_client = BybitClient(
        testnet=config.testnet,
        max_retries=config.max_api_retries,
        retry_backoff_cap_seconds=config.retry_backoff_cap_seconds,
    )
    live_broker = LiveBroker(client=bybit_client, symbol=symbol, dry_run=config.dry_run)

    runner = StrategyRunner(
        entry=strategy.entry,
        exit=strategy.exit,
        time_window=strategy.time_window,
        order_qty=strategy.order_qty,
        broker=live_broker,
        kill_switch=strategy.kill_switch,
    )
    return runner, symbol


def get_current_price(runner: StrategyRunner, config: ExecutionConfig, symbol: str) -> float:
    """對應 sat_strategy/app/bot.py 的 _get_last_price():優先用
    account_feed/market_feed 的即時價格,沒資料才 fallback 回直接
    呼叫 Bybit REST API 查。"""
    if config.use_live_ticker_feed:
        live_price = ticker_feed.get_last_price()
        if live_price is not None:
            return live_price
        logger.debug("[market_feed] 還沒有即時價格可用,fallback 回 REST。")

    live_broker: LiveBroker = runner.broker  # type: ignore[assignment]
    return live_broker.client.get_last_price(symbol)


def run_forever(
    runner: StrategyRunner,
    config: ExecutionConfig,
    symbol: str,
    now_fn: Callable[[], datetime] = lambda: datetime.now(HKT),
) -> None:
    if config.use_live_ticker_feed:
        ticker_feed.start()

    def handle_stop_signal(signum, frame):
        logger.warning(f"收到停止訊號({signum}),準備清理後結束")
        runner.request_stop()

    signal.signal(signal.SIGTERM, handle_stop_signal)
    signal.signal(signal.SIGINT, handle_stop_signal)

    now = now_fn()
    price = get_current_price(runner, config, symbol)
    logger.info(f"啟動 strategy_lab live runner,起始價格 = {price:.2f}")
    runner.start(now, price)
    runner.tick(now, price)

    while runner.state != RunState.STOPPED:
        time.sleep(config.poll_interval_seconds)
        now = now_fn()
        price = get_current_price(runner, config, symbol)
        runner.tick(now, price)

    if config.use_live_ticker_feed:
        ticker_feed.stop()
    logger.info(f"strategy_lab live runner 結束,共完成 {len(runner.trades)} 筆交易")


def main() -> None:
    load_dotenv()
    config = load_execution_config()
    logger.info(f"載入執行參數: {config.to_dict()}")

    if config.dry_run:
        logger.warning("=== DRY RUN 模式:不會真的下單,只會記錄 log ===")
    if config.testnet:
        logger.warning("=== 使用 Bybit 測試網(testnet) ===")
    else:
        logger.warning("=== 使用 Bybit 正式環境,將動用真實資金! ===")

    runner, symbol = build_runner_and_symbol(config)
    run_forever(runner, config, symbol)


if __name__ == "__main__":
    main()
