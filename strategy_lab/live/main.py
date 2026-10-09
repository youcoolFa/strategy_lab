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
    .venv/bin/python -m strategy_lab.live.main                            # 讀 live_execution_config.yaml
    .venv/bin/python -m strategy_lab.live.main --config live_wld_long.yaml  # 每個策略一份設定檔
同時跑多個策略時,每個策略用自己的設定檔、而且要是不同的 symbol(同一個
symbol 在 Bybit 單向持倉模式下共用一個部位,會互相平掉對方的倉位)。

實盤建議用 live/daemon.py 在背景跑,不要直接跑在 IDE 的終端機裡:
    .venv/bin/python -m strategy_lab.live.daemon start --config live_btc_band.yaml
直接在終端機跑時,Ctrl+C / 關掉終端機(SIGHUP)/ kill(SIGTERM)都會走收尾;
但 IDE 如果強制砍掉整個 process,任何程式都來不及收尾。
"""

from __future__ import annotations

import argparse
import os
import platform
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from loguru import logger

from strategy_lab.dsl.loader import load_strategy
from strategy_lab.dsl.order_config import OrderConfig, compute_qty
from strategy_lab.engine.events import Event, summarize
from strategy_lab.engine.hold_time import format_hold, hold_summary
from strategy_lab.engine.runner import RunState, StrategyRunner
from strategy_lab.engine.scale_in_runner import ScaleInRunner
from strategy_lab.live.broker import LiveBroker
from strategy_lab.live.bybit_client import NETWORK_ERRORS, BybitClient
from strategy_lab.live.config import ExecutionConfig, load_execution_config
from strategy_lab.live.control import RUN_DIR as CONTROL_RUN_DIR, process_control
from strategy_lab.live.market_feed import ticker_feed
from strategy_lab.live.status import (
    StatusReporter, cost_totals, fee_ratio, loop_progress, mode_label, net_summary, px, start_message,
    stop_reason_label, usd,
)
from strategy_lab.log.logger_setup import add_file_sink, setup_logger
from strategy_lab.storage.recorder import RunInfo, TradeRecorder

HKT = ZoneInfo("Asia/Hong_Kong")
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
PENDING_DIR = LOG_DIR / "db_pending"


def setup_file_logging(name: str, log_dir: Optional[Path] = None) -> Tuple[Path, int]:
    """除了終端機,log 也寫一份到 logs/<設定檔名>_<啟動時間>.log。終端機
    一關 log 就沒了(2026-09-27 事故只能靠 PyCharm/zsh 的紀錄反推原因)。"""
    return add_file_sink(name, log_dir if log_dir is not None else LOG_DIR)


def _detach_from_closed_terminal() -> None:
    # 終端機已經關掉,之後寫 stdout/stderr 會出錯;導到 /dev/null,log 檔照寫。
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)


def install_stop_signal_handlers(runner: StrategyRunner) -> None:
    """SIGINT(Ctrl+C)、SIGTERM(kill / daemon stop)、SIGHUP(關掉終端機或
    PyCharm)都走同一個收尾:下一次 tick() 取消掛單、平倉。原本漏了 SIGHUP,
    2026-09-27 關掉 PyCharm 時程式被直接砍掉,WLD 掛單留在交易所上沒人管。"""

    def handle_stop_signal(signum, frame):
        if signum == signal.SIGHUP:
            _detach_from_closed_terminal()
        logger.warning(f"收到停止訊號({signal.Signals(signum).name}),準備清理後結束")
        runner.request_stop()

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, handle_stop_signal)

    def handle_detach_signal(signum, frame):
        logger.warning("收到脫離訊號(SIGUSR1):這個 tick 結束後直接結束,不收尾,掛單與持倉留在交易所")
        runner.request_detach()

    signal.signal(signal.SIGUSR1, handle_detach_signal)


def to_bybit_symbol(yaml_symbol: str) -> str:
    """YAML 裡的 symbol 是給人看的格式(如 "BTC/USDT"、ccxt 風格的
    "BTC/USDT:USDT"),Bybit 原生 API 要的是不帶分隔符的格式(如
    "BTCUSDT")。"""
    return yaml_symbol.split(":")[0].replace("/", "")


def _resolve_order_qty(config: ExecutionConfig, bybit_client: BybitClient, symbol: str,
                       price: Optional[float] = None) -> float:
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
    current_price = price if price is not None else bybit_client.get_last_price(symbol)
    return compute_qty(order_config, current_price=current_price)


def scale_in_entry_prices(config: ExecutionConfig) -> List[float]:
    """分注策略每一注的建倉價,只從 live_execution_config.yaml 的 entry_prices 讀
    (不用 origin_price)。沒填就報錯,不猜價格。"""
    if not config.entry_prices:
        raise ValueError(
            "分注策略(scale_in: true)要在 live_execution_config.yaml 填 entry_prices,"
            "每注一個建倉價,例 entry_prices: [84900, 84500, 84000]"
        )
    return [float(p) for p in config.entry_prices]


def apply_strategy_overrides(strategy: Any, config: ExecutionConfig) -> Any:
    """套用 live_execution_config.yaml 的 strategy_overrides(運作中用 live/control.py 改過、寫回的參數),
    讓重新啟動和 preflight 都用改過的值。只有 loop 與分注策略的平倉距離可以覆蓋。"""
    overrides = config.strategy_overrides or {}
    unknown = sorted(set(overrides) - {"loop", "exit_distance"})
    if unknown:
        raise ValueError(f"strategy_overrides 不認得 {unknown};只能有 loop、exit_distance")
    if "loop" in overrides:
        loop = overrides["loop"]
        if loop is not None and (isinstance(loop, bool) or not isinstance(loop, int) or loop < 0):
            raise ValueError(f"strategy_overrides.loop 要是 0 以上的整數或 null,收到 {loop!r}")
        strategy.loop = loop
    if "exit_distance" in overrides:
        from strategy_lab.plugins.exit.scale_out import ScaleOutExit

        if not isinstance(strategy.exit, ScaleOutExit):
            raise ValueError("strategy_overrides.exit_distance 只能用在分注策略(exit: scale_out)")
        strategy.exit = ScaleOutExit(distance=dict(overrides["exit_distance"]))
    return strategy


def build_runner_and_symbol(config: ExecutionConfig, recorder: Optional[Any] = None) -> Tuple[StrategyRunner, str]:
    strategy = apply_strategy_overrides(load_strategy(config.strategy_path), config)
    symbol = config.symbol_override or to_bybit_symbol(strategy.symbol)

    bybit_client = BybitClient(
        testnet=config.testnet,
        max_retries=config.max_api_retries,
        retry_backoff_cap_seconds=config.retry_backoff_cap_seconds,
        category=config.category,
        persist_retry_seconds=config.poll_interval_seconds,
    )
    live_broker = LiveBroker(client=bybit_client, symbol=symbol, dry_run=config.dry_run)
    common = dict(
        entry=strategy.entry,
        exit=strategy.exit,
        time_window=strategy.time_window,
        broker=live_broker,
        kill_switch=strategy.kill_switch,
        order_type=config.order_type,
        direction=strategy.direction,
        loop=strategy.loop,
        on_event=log_event,
    )
    if strategy.scale_in:
        entry_prices = scale_in_entry_prices(config)
        # 第一注數量:fixed_quote_amount / account_percentage 用第一注的建倉價換算
        order_qty = _resolve_order_qty(config, bybit_client, symbol, price=entry_prices[0])
        runner: StrategyRunner = ScaleInRunner(order_qty=order_qty, entry_prices=entry_prices,
                                               expected_hold=strategy.expected_hold, **common)
    else:
        order_qty = _resolve_order_qty(config, bybit_client, symbol)
        runner = StrategyRunner(order_qty=order_qty, **common)
    attach_recorder(runner, recorder)
    return runner, symbol


def make_recorder(config: ExecutionConfig, client: Any) -> Optional[TradeRecorder]:
    """實盤才記錄(dry-run 不存)。TRADING_DB_URL 沒設或連不上時,紀錄寫本機
    `logs/db_pending/`,之後用 storage/backfill.py 補進資料庫。"""
    if config.dry_run:
        return None
    return TradeRecorder(db_url=os.getenv("TRADING_DB_URL"), pending_dir=PENDING_DIR, fetch_executions=client.get_executions)


def attach_recorder(runner: StrategyRunner, recorder: Optional[Any]) -> None:
    if recorder is None:
        return

    def on_event(event: Event) -> None:
        log_event(event)
        recorder.record_event(event)

    runner.on_order = recorder.record_order
    runner.on_event = on_event


def _run_info(runner: StrategyRunner, config: ExecutionConfig, symbol: str, origin: Optional[float],
              origin_source: Optional[str], started_at: datetime, log_path: Optional[Path],
              preflight: Optional[Dict[str, Any]]) -> RunInfo:
    import yaml

    strategy_yaml = Path(config.strategy_path).read_text(encoding="utf-8")
    params = yaml.safe_load(strategy_yaml) or {}
    return RunInfo(
        strategy_name=params.get("name", Path(config.strategy_path).stem), strategy_path=config.strategy_path,
        strategy_yaml=strategy_yaml, strategy_params=params, config=config.to_dict(), symbol=symbol,
        category=config.category, direction=runner.direction, origin_price=origin, origin_source=origin_source,
        qty=runner.order_qty, order_type=config.order_type, loop=runner.loop, testnet=config.testnet,
        preflight=preflight, log_path=str(log_path) if log_path else None, started_at=started_at,
    )


_PURPOSE_LABELS = {"entry": "進場", "exit": "平倉", "forced_close": "強制平倉"}


def attach_notifications(runner: Any, dry_run: bool, costs: Any = None) -> None:
    """實盤時,每筆成交、每輪(event)完成都發一則 Telegram(包住原本的 on_order/on_event,
    資料庫紀錄照常)。dry-run 不發——假成交每幾秒一輪,會洗版。"""
    if dry_run:
        return
    prev_order, prev_event = runner.on_order, runner.on_event
    fills = logger.bind(telegram=True, category="fill")  # 🟢 #成交
    pnl = logger.bind(telegram=True, category="pnl")  # 🟣 #損益

    def on_order(rec: Any) -> None:
        if prev_order is not None:
            prev_order(rec)
        if rec.status == "closed":
            lot = f"(第{rec.lot}注)" if rec.lot else ""
            held = getattr(rec, "hold_seconds", None)
            hold = f"|持倉 {format_hold(timedelta(seconds=held))}" if held is not None else ""
            fills.info(
                f"✅ 成交|{_PURPOSE_LABELS.get(rec.purpose, rec.purpose)}{lot} {rec.side} {rec.filled_qty:g} "
                f"@ {px(rec.avg_price)}{hold}|{loop_progress(rec.event_index, getattr(runner, 'loop', None))}"
            )

    def on_event(event: Event) -> None:
        if prev_event is not None:
            prev_event(event)
        how = "強制平倉" if event.forced else "正常平倉"
        done = list(getattr(runner, "events", []))
        if event not in done:
            done.append(event)
        this = costs.event_costs(event.index) if costs is not None else None
        if this is not None:
            fees, funding = this
            result = (f"這輪淨利 {usd(event.realized_pnl - fees - funding)} USDT"
                      f"(毛利 {usd(event.realized_pnl)} − 手續費 {fees:.4f} − 資金費 {funding:.4f})"
                      f"|{fee_ratio(event.realized_pnl, fees)}")
        else:
            result = f"這輪毛利 {usd(event.realized_pnl)} USDT(手續費待查,收尾時補算進資料庫)"
        total = net_summary(done, costs) or (
            f"累計毛利 {usd(sum(e.realized_pnl for e in done))} USDT({len(done)} 個 loop,未扣手續費)")
        pnl.info(
            f"💰 {loop_progress(event.index, getattr(runner, 'loop', None))} 完成({how})\n"
            f"{event.direction} 均價 {px(event.avg_entry)} → {px(event.avg_exit)},成交 {event.fills} 次\n"
            f"{result}\n期間最大回撤 {usd(event.max_drawdown)} USDT\n{total}"
        )

    runner.on_order = on_order
    runner.on_event = on_event


def log_event(event: Event) -> None:
    how = "強制平倉" if event.forced else "正常平倉"
    logger.info(
        f"event #{event.index} 完成({how}):{event.direction} 均價 {event.avg_entry} → {event.avg_exit},"
        f"成交 {event.fills} 次,最大部位 {event.max_position},損益 {event.realized_pnl:+.4f} USDT(未扣手續費),"
        f"期間最大回撤 {event.max_drawdown:+.4f} USDT,持倉 {event.end_time - event.start_time}"
    )


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


def ensure_clean_start(client, symbol: str, dry_run: bool, adopt: bool = False) -> Optional[Tuple[list, float]]:
    """當機或被強制關閉時 _cleanup() 沒跑,舊掛單/部位還留在交易所上;新
    process 不知道它們存在,直接啟動會再掛一張,變成兩倍部位。有殘留就
    拒絕啟動,讓人先到 Bybit 手動處理。

    adopt=True(設定檔 adopt_existing_position: true):有殘留時不拒絕,回傳 (掛單, 持倉) 給
    adopt_existing() 接手;乾淨就回傳 None。"""
    if dry_run:
        return None
    orders = client.get_open_orders(symbol)
    position = client.get_position_qty(symbol)
    if not orders and position == 0:
        return None
    if adopt:
        return orders, position
    lines = [f"{symbol} 在交易所上還有上次留下的東西,拒絕啟動(避免重複掛單/重複開倉):"]
    for o in orders:
        lines.append(f"  掛單 {o.get('orderId')} {o.get('side')} @ {o.get('price')} qty={o.get('qty')}")
    if position != 0:
        lines.append(f"  持倉 {position}")
    lines.append("請先到 Bybit 取消這些掛單/平掉持倉,再重新啟動;分注策略也可以在設定檔設 "
                 "adopt_existing_position: true,不平倉直接接手。")
    raise LeftoverExchangeStateError("\n".join(lines))


def adopt_existing(runner: Any, client: Any, symbol: str, orders: list, position: float, now: datetime) -> List[str]:
    """把交易所上的持倉/掛單對應回分注策略的每一注並裝進 runner(不下單、不取消)。對不上 → 拒絕啟動。"""
    from strategy_lab.live.adopt import AdoptionError, apply_adoption, plan_adoption

    if not isinstance(runner, ScaleInRunner):
        raise LeftoverExchangeStateError("接手現有持倉目前只支援分注策略(scale_in);請先到 Bybit 手動處理殘留掛單/持倉")
    try:
        plan = plan_adoption(runner.entry_prices, runner.lot_qtys, runner.exit, runner.direction, orders, position,
                             client.get_position_avg_price(symbol) if position else 0.0)
    except AdoptionError as e:
        raise LeftoverExchangeStateError(f"無法接手 {symbol} 的現有持倉/掛單,拒絕啟動:{e}") from e
    return apply_adoption(runner, plan, now)


def resolve_origin_price(config: ExecutionConfig, current_price: float) -> float:
    if config.origin_price is None:
        return current_price
    if config.origin_price <= 0:
        raise ValueError(f"origin_price 必須大於 0,目前是 {config.origin_price}")
    return config.origin_price


def _warn_tick_network_error(config: ExecutionConfig, error: Exception) -> None:
    logger.warning(
        f"這一輪網路持續失敗(重試已用完),{config.poll_interval_seconds} 秒後下一輪再試,不放棄"
        f"(掛單/部位還在交易所上,不能就這樣當掉): {error}"
    )


def _tick_tolerating_network_errors(runner: StrategyRunner, now: datetime, price: float, config: ExecutionConfig) -> None:
    """網路持續失敗時不讓整個程式當掉(對齊 sat_strategy「不放棄」):這一輪
    放棄,下一輪重來。runner 的每一步都設計成失敗後下一輪重做是安全的——
    下單有 orderLinkId 防重複、收尾一旦開始每一輪都會重跑到完成(見
    docs/ARCHITECTURE.md §6.23)。業務錯誤(InvalidRequestError,例如餘額不足)
    不在此列,照樣往上拋。"""
    try:
        runner.tick(now, price)
    except NETWORK_ERRORS as e:
        _warn_tick_network_error(config, e)


def run_forever(
    runner: StrategyRunner,
    config: ExecutionConfig,
    symbol: str,
    now_fn: Callable[[], datetime] = lambda: datetime.now(HKT),
    recorder: Optional[Any] = None,
    log_path: Optional[Path] = None,
    preflight: Optional[Dict[str, Any]] = None,
    config_path: Optional[Path] = None,
    control_run_dir: Optional[Path] = None,
) -> None:
    live_broker: LiveBroker = runner.broker  # type: ignore[assignment]
    try:
        leftover = ensure_clean_start(live_broker.client, symbol, config.dry_run, adopt=config.adopt_existing_position)
    except LeftoverExchangeStateError:
        if recorder is not None:
            now = now_fn()
            recorder.start_run(_run_info(runner, config, symbol, None, None, now, log_path, preflight))
            recorder.end_run(now, "refused_leftover")
        raise

    if config.use_live_ticker_feed:
        ticker_feed.start()

    install_stop_signal_handlers(runner)

    now = now_fn()
    price = get_current_price(runner, config, symbol)
    origin = resolve_origin_price(config, price)
    source = "手動輸入" if config.origin_price is not None else "啟動當下即時價"
    if isinstance(runner, ScaleInRunner):
        lots = ", ".join(f"第{i}注 {p} × {q:g}" for i, p, q in runner.active_lots)
        logger.info(f"啟動 strategy_lab live runner(分注):{lots};目前價格 = {price}(origin_price 不使用)")
        origin, source = None, None  # 分注策略不用 origin_price;sl_run 記 NULL
    else:
        logger.info(
            f"啟動 strategy_lab live runner:origin_price = {origin}({source}),"
            f"目前價格 = {price},差 {(price / origin - 1) * 100:+.3f}%"
        )
    if recorder is not None:
        recorder.start_run(_run_info(runner, config, symbol, origin, source, now, log_path, preflight))
    try:
        runner.start(now, origin if origin is not None else price)
        reporter = StatusReporter(runner, config, symbol, started_at=now, costs=recorder)
        logger.bind(telegram=True).info(start_message(runner, config, symbol, price, now, reporter.interval))
        if leftover is not None:
            adopted = adopt_existing(runner, live_broker.client, symbol, leftover[0], leftover[1], now)
            logger.bind(telegram=True, category="position").info(
                "🔁 接手現有持倉(沒有平倉,沿用交易所上的單)\n" + "\n".join(adopted)
                + "\n注意:loop 從這次啟動重新算;舊程式的建倉手續費不在這次的淨利裡")
        _tick_tolerating_network_errors(runner, now, price, config)

        while runner.state != RunState.STOPPED and not runner.detach_requested:
            time.sleep(config.poll_interval_seconds)
            now = now_fn()
            try:
                price = get_current_price(runner, config, symbol)
            except NETWORK_ERRORS as e:
                _warn_tick_network_error(config, e)
                continue
            _tick_tolerating_network_errors(runner, now, price, config)
            reporter.maybe_report(now, price)  # 每 STATUS_INTERVAL_MINUTES 分鐘一則 ⚪ #狀態
            # 運作中改參數(live/control.py 寫的請求檔):在兩個 tick 之間套用,不會跟成交處理互相干擾
            if runner.state != RunState.STOPPED:
                process_control(runner, config_path, now, price, control_run_dir or CONTROL_RUN_DIR)
    except Exception:
        if recorder is not None:
            recorder.end_run(now_fn(), "crash")
        raise

    if config.use_live_ticker_feed:
        ticker_feed.stop()
    if runner.detach_requested:
        logger.bind(telegram=True).warning(
            "🔌 已脫離:程式結束但沒有收尾,掛單與持倉保留在交易所上。下次在設定檔打開 adopt_existing_position "
            "再啟動就會接手;不接手的話請到 Bybit 自行處理。")
        if recorder is not None:
            recorder.end_run(now_fn(), "detached", summarize(runner.events))
        return
    summary = summarize(runner.events)
    logger.info(
        f"strategy_lab live runner 結束:event {summary.count} 個(其中強制平倉 {summary.forced} 個,獲利 {summary.wins} 個),"
        f"合計損益 {summary.total_pnl:+.4f} USDT(未扣手續費),最大回撤 {summary.max_drawdown:+.4f} USDT"
    )
    if recorder is not None:
        recorder.end_run(now_fn(), runner.stop_reason or "unknown", summary)  # 先補同步成交明細,結算才有手續費
    totals = cost_totals(runner.events, recorder)
    if totals is not None:
        gross, fees, funding = totals
        total = (f"合計淨利 {usd(gross - fees - funding)} USDT(毛利 {usd(gross)} − 手續費 {fees:.4f} − 資金費 "
                 f"{funding:.4f})|{fee_ratio(gross, fees)}")
    else:
        total = f"合計毛利 {usd(summary.total_pnl)} USDT(未扣手續費)"
    logger.bind(telegram=True, category="pnl").info(
        f"🏁 strategy_lab 結束:{stop_reason_label(runner.stop_reason)}\n"
        f"完成 {summary.count} 個 loop(強制平倉 {summary.forced}、獲利 {summary.wins})\n"
        f"{total},最大回撤 {usd(summary.max_drawdown)} USDT"
        + (f"\n{holds}" if (holds := hold_summary(getattr(runner, "lot_holds", []),
                                                   getattr(runner, "expected_hold", None))) else "")
    )


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

    log_name = config_path.stem if config_path is not None else "live_execution_config"
    log_path, _ = setup_logger(log_name, log_dir=LOG_DIR)
    logger.info(f"log 檔: {log_path}")
    logger.info(f"Python 直譯器: {sys.executable}({platform.python_version()})")

    load_dotenv()
    config = load_execution_config(config_path=config_path)
    logger.info(f"載入執行參數: {config.to_dict()}")

    if config.dry_run:
        logger.bind(telegram=False).warning("=== DRY RUN 模式:不會真的下單,只會記錄 log ===")
    if config.testnet:
        logger.bind(telegram=False).warning("=== 使用 Bybit 測試網(testnet) ===")
    else:
        logger.bind(telegram=False).warning("=== 使用 Bybit 正式環境,將動用真實資金! ===")

    from strategy_lab.live.preflight import load_snapshot  # preflight 也 import 這個模組,放在函式內避免循環 import

    preflight = load_snapshot(config_path or Path(__file__).resolve().parents[2] / "live_execution_config.yaml")
    try:
        runner, symbol = build_runner_and_symbol(config)
        recorder = None if config.dry_run else make_recorder(config, runner.broker.client)
        attach_recorder(runner, recorder)
        attach_notifications(runner, config.dry_run, costs=recorder)
        run_forever(runner, config, symbol, recorder=recorder, log_path=log_path, preflight=preflight,
                    config_path=config_path or Path(__file__).resolve().parents[2] / "live_execution_config.yaml")
    except Exception:
        logger.exception("live runner 異常結束(沒有走收尾流程,請檢查交易所上的掛單/持倉)")
        raise


if __name__ == "__main__":
    main()
