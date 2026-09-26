"""
live/main.py

真正的執行入口,對應 sat_strategy/app/bot.py 的 main()/SatStrategyBot.run()。

流程:
    1. load_dotenv() 讀 .env(真實 API key、REDIS_URL 這些不進 git 的值)
    2. load_execution_config() 讀執行參數(dry_run/testnet/輪詢間隔...)
    3. dsl.loader.load_strategy() 讀策略(entry/exit/time_window/
       kill_switch,參數都在 YAML 裡,這個檔案不重複定義)
    4. 組出 BybitClient -> LiveBroker -> StrategyRunner
    4b. ensure_clean_start():交易所上這個 symbol 還有上次留下的掛單/持倉
        (當機後 _cleanup() 沒跑)就拒絕啟動,避免重複掛單
    5. 如果 use_live_ticker_feed,啟動 market_feed.ticker_feed
    6. 用真實時鐘驅動的迴圈跑 runner.tick(),直到 STOPPED
    7. 收到 SIGINT/SIGTERM 呼叫 runner.request_stop(),讓下一次 tick()
       觸發正常的 _cleanup() 收尾,不是直接砍掉 process 留下沒人管的
       真實掛單或部位

執行方式:
    /opt/anaconda3/bin/python3 -m strategy_lab.live.main                            # 讀 live_execution_config.yaml
    /opt/anaconda3/bin/python3 -m strategy_lab.live.main --config live_wld_long.yaml  # 每個策略一份設定檔
同時跑多個策略時,每個策略用自己的設定檔、而且要是不同的 symbol(同一個
symbol 在 Bybit 單向持倉模式下共用一個部位,會互相平掉對方的倉位)。
"""

from __future__ import annotations

import argparse
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from loguru import logger

from strategy_lab.dsl.loader import load_strategy
from strategy_lab.dsl.order_config import OrderConfig, compute_qty
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


def _resolve_order_qty(config: ExecutionConfig, bybit_client: BybitClient, symbol: str) -> float:
    """把 config.position_sizing 換算成真正的下單數量。`fixed_qty` 不用
    知道價格,直接回傳,不會多打一次網路請求——這是
    test_dry_run_false_still_builds_without_real_network_call 在測的
    行為:預設設定下,建構 runner 這件事本身不該發任何真實請求。

    `account_percentage` 模式:如果 `config.account_value` 沒有明確設
    (使用者沒有手動覆蓋),就真的去查一次帳戶權益
    (`BybitClient.get_account_equity()`)——這是需要驗證的端點,只有
    這個模式才會用到。使用者仍然可以在設定檔手動填一個 `account_value`
    覆蓋掉真實查詢結果(例如想用比真實權益更保守的假設值算 qty)。"""
    account_value = config.account_value
    if config.position_sizing.mode == "account_percentage" and account_value is None:
        account_value = bybit_client.get_account_equity()

    order_config = OrderConfig(
        symbol=symbol,
        order_type=config.order_type,
        position_sizing=config.position_sizing,
        account_value=account_value,
    )
    if config.position_sizing.mode == "fixed_qty":
        return compute_qty(order_config, current_price=0.0)
    current_price = bybit_client.get_last_price(symbol)
    return compute_qty(order_config, current_price=current_price)


def build_runner_and_symbol(config: ExecutionConfig) -> Tuple[StrategyRunner, str]:
    strategy = load_strategy(config.strategy_path)
    symbol = config.symbol_override or to_bybit_symbol(strategy.symbol)

    bybit_client = BybitClient(
        testnet=config.testnet,
        max_retries=config.max_api_retries,
        retry_backoff_cap_seconds=config.retry_backoff_cap_seconds,
        category=config.category,
    )
    live_broker = LiveBroker(client=bybit_client, symbol=symbol, dry_run=config.dry_run)
    order_qty = _resolve_order_qty(config, bybit_client, symbol)

    runner = StrategyRunner(
        entry=strategy.entry,
        exit=strategy.exit,
        time_window=strategy.time_window,
        order_qty=order_qty,
        broker=live_broker,
        kill_switch=strategy.kill_switch,
        order_type=config.order_type,
        direction=strategy.direction,
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


class LeftoverExchangeStateError(RuntimeError):
    pass


def ensure_clean_start(client, symbol: str, dry_run: bool) -> None:
    """當機或被強制關閉時 _cleanup() 沒跑,舊掛單/部位還留在交易所上;新
    process 不知道它們存在,直接啟動會再掛一張,變成兩倍部位。有殘留就
    拒絕啟動,讓人先到 Bybit 手動處理。"""
    if dry_run:
        return
    orders = client.get_open_orders(symbol)
    position = client.get_position_qty(symbol)
    if not orders and position == 0:
        return
    lines = [f"{symbol} 在交易所上還有上次留下的東西,拒絕啟動(避免重複掛單/重複開倉):"]
    for o in orders:
        lines.append(f"  掛單 {o.get('orderId')} {o.get('side')} @ {o.get('price')} qty={o.get('qty')}")
    if position != 0:
        lines.append(f"  持倉 {position}")
    lines.append("請先到 Bybit 取消這些掛單/平掉持倉,再重新啟動。")
    raise LeftoverExchangeStateError("\n".join(lines))


def resolve_origin_price(config: ExecutionConfig, current_price: float) -> float:
    if config.origin_price is None:
        return current_price
    if config.origin_price <= 0:
        raise ValueError(f"origin_price 必須大於 0,目前是 {config.origin_price}")
    return config.origin_price


def run_forever(
    runner: StrategyRunner,
    config: ExecutionConfig,
    symbol: str,
    now_fn: Callable[[], datetime] = lambda: datetime.now(HKT),
) -> None:
    live_broker: LiveBroker = runner.broker  # type: ignore[assignment]
    ensure_clean_start(live_broker.client, symbol, config.dry_run)

    if config.use_live_ticker_feed:
        ticker_feed.start()

    def handle_stop_signal(signum, frame):
        logger.warning(f"收到停止訊號({signum}),準備清理後結束")
        runner.request_stop()

    signal.signal(signal.SIGTERM, handle_stop_signal)
    signal.signal(signal.SIGINT, handle_stop_signal)

    now = now_fn()
    price = get_current_price(runner, config, symbol)
    origin = resolve_origin_price(config, price)
    source = "手動輸入" if config.origin_price is not None else "啟動當下即時價"
    logger.info(
        f"啟動 strategy_lab live runner:origin_price = {origin}({source}),"
        f"目前價格 = {price},差 {(price / origin - 1) * 100:+.3f}%"
    )
    runner.start(now, origin)
    runner.tick(now, price)

    while runner.state != RunState.STOPPED:
        time.sleep(config.poll_interval_seconds)
        now = now_fn()
        price = get_current_price(runner, config, symbol)
        runner.tick(now, price)

    if config.use_live_ticker_feed:
        ticker_feed.stop()
    logger.info(f"strategy_lab live runner 結束,共完成 {len(runner.trades)} 筆交易")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="strategy_lab live runner")
    parser.add_argument(
        "--config",
        default=None,
        help="執行設定檔路徑;同時跑多個策略時每個策略用自己的一份,斷線重啟用同一行指令。不給就用 live_execution_config.yaml",
    )
    args = parser.parse_args(argv)

    config_path: Optional[Path] = None
    if args.config is not None:
        config_path = Path(args.config)
        if not config_path.exists():
            raise FileNotFoundError(f"找不到設定檔 {config_path}(不會退回預設值,避免跑錯策略)")

    load_dotenv()
    config = load_execution_config(config_path=config_path)
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
