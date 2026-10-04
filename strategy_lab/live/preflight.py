"""
live/preflight.py

啟動前確認:讀設定檔和策略,查真實市場資料(唯讀),顯示掛單計畫、每輪
損益、風險、時間窗,輸入確認才用 live/daemon.py 在背景啟動。

    .venv/bin/python -m strategy_lab.live.preflight --config live_execution_config.yaml

刻意跟 live/main.py 分開:main.py 要能無人值守啟動,不能停下來等輸入。
實盤要輸入完整的 `yes` 才啟動;dry-run 輸入 `y` 即可。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from strategy_lab.dsl.loader import load_strategy
from strategy_lab.estimates.metrics import run_metrics
from strategy_lab.estimates.model import Estimate, MarketSnapshot
from strategy_lab.estimates.plan import build_order_plan, build_scale_in_plan
from strategy_lab.live import daemon
from strategy_lab.live.bybit_client import BybitClient
from strategy_lab.live.config import ExecutionConfig, load_execution_config
from strategy_lab.live.instrument_limits import UnknownSymbolError, fix_qty, load_instrument_limits
from strategy_lab.live.main import _resolve_order_qty, resolve_origin_price, scale_in_entry_prices, to_bybit_symbol

HKT = ZoneInfo("Asia/Hong_Kong")
SNAPSHOT_DIR = daemon.RUN_DIR
SNAPSHOT_MAX_AGE = timedelta(minutes=30)


def _snapshot_path(config_path: Path, snapshot_dir: Optional[Path]) -> Path:
    return Path(snapshot_dir or SNAPSHOT_DIR) / f"{Path(config_path).stem}.preflight.json"


def save_snapshot(config_path: Path, results, snapshot_dir: Optional[Path] = None) -> None:
    """確認啟動時把剛顯示的估算存起來,live/main.py 啟動後讀進 sl_run.preflight。"""
    data = {
        "saved_at": datetime.now(HKT).isoformat(),
        "config": str(config_path),
        "metrics": {r.title: {row.key: row.value for row in r.rows} for r in results},
        "texts": {r.title: {row.key: row.text for row in r.rows} for r in results},
    }
    path = _snapshot_path(config_path, snapshot_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_snapshot(config_path: Path, snapshot_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """讀一次就刪;超過 30 分鐘的視為過期(不是這次 preflight 確認的)。"""
    path = _snapshot_path(config_path, snapshot_dir)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()
    if datetime.now(HKT) - datetime.fromisoformat(data["saved_at"]) > SNAPSHOT_MAX_AGE:
        return None
    return data


def _default_client(config: ExecutionConfig) -> BybitClient:
    return BybitClient(
        testnet=config.testnet,
        max_retries=config.max_api_retries,
        retry_backoff_cap_seconds=config.retry_backoff_cap_seconds,
        category=config.category,
    )


def _fix_qty_or_report(raw_qty: float, limits, what: str, print_fn) -> Optional[float]:
    """修正到交易所精度;低於最小下單量回傳 None(並印出原因),呼叫端就不啟動。"""
    if limits is None:
        return raw_qty
    try:
        return fix_qty(raw_qty, limits, order_type="limit")
    except ValueError as e:
        print_fn(f"✗ {what}數量 {raw_qty:.6f} 低於交易所最小下單量 {limits.min_qty}:{e}")
        print_fn("請調高 position_sizing 後再試。沒有啟動。")
        return None


def main(
    argv: Optional[List[str]] = None,
    client_factory: Callable[[ExecutionConfig], object] = _default_client,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
    start_fn: Optional[Callable[[Path], object]] = None,
    now_fn: Callable[[], datetime] = lambda: datetime.now(HKT),
) -> int:
    parser = argparse.ArgumentParser(description="啟動前顯示估算,確認後在背景啟動 live runner")
    parser.add_argument("--config", default=str(daemon.DEFAULT_CONFIG), help="執行設定檔,預設 live_execution_config.yaml")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(f"找不到設定檔 {config_path}")

    load_dotenv()
    config = load_execution_config(config_path=config_path)
    strategy = load_strategy(config.strategy_path)
    symbol = config.symbol_override or to_bybit_symbol(strategy.symbol)
    client = client_factory(config)

    now = now_fn()
    price = client.get_last_price(symbol)
    maker, taker = client.get_fee_rates(symbol)
    market = MarketSnapshot(
        symbol=symbol, price=price, equity=client.get_account_equity(),
        maker_fee_rate=maker, taker_fee_rate=taker,
        leverage=client.get_leverage(symbol), margin_mode=client.get_margin_mode(), now=now,
    )

    print_fn(f"策略 {strategy.name}({strategy.direction}) | {symbol} | 設定檔 {config_path}")
    if config.dry_run:
        print_fn("=== DRY RUN 模式:不會真的下單 ===")
    else:
        print_fn(f"=== {'測試網' if config.testnet else '正式環境'}:會用真實資金下單 ===")

    try:
        limits = load_instrument_limits(symbol)
    except (UnknownSymbolError, FileNotFoundError):
        limits = None
        print_fn(f"⚠ instrument_limits.json 沒有 {symbol} 的精度資料,價格/數量不會修正")

    window_end = strategy.time_window.window_end(now)
    cleanup_at = window_end - timedelta(minutes=getattr(strategy.time_window, "cleanup_buffer_minutes", 0))

    if strategy.scale_in:
        try:
            entry_prices = scale_in_entry_prices(config)
            if len(entry_prices) != strategy.entry.lots:
                raise ValueError(f"entry_prices 要有 {strategy.entry.lots} 個價格(每注一個),目前是 {entry_prices}")
        except ValueError as e:
            print_fn(f"✗ {e}。沒有啟動。")
            return 1
        raw_qtys = strategy.entry.lot_qtys(_resolve_order_qty(config, client, symbol, price=entry_prices[0]))
        qtys = []
        for i, raw in enumerate(raw_qtys, 1):
            fixed = _fix_qty_or_report(raw, limits, f"第{i}注", print_fn)
            if fixed is None:
                return 1
            qtys.append(fixed)
        plan = build_scale_in_plan(
            strategy_name=strategy.name, entry=strategy.entry, exit=strategy.exit, direction=strategy.direction,
            entry_prices=entry_prices, qtys=qtys, market=market, cleanup_at=cleanup_at, limits=limits,
            loop=strategy.loop,
        )
    else:
        qty = _fix_qty_or_report(_resolve_order_qty(config, client, symbol), limits, "下單", print_fn)
        if qty is None:
            return 1
        origin = resolve_origin_price(config, price)
        plan = build_order_plan(
            strategy_name=strategy.name, entry=strategy.entry, exit=strategy.exit, direction=strategy.direction,
            origin_price=origin, origin_source="手動輸入" if config.origin_price is not None else "啟動當下即時價",
            qty=qty, market=market, cleanup_at=cleanup_at, limits=limits, loop=strategy.loop,
        )

    results = run_metrics(Estimate(plan=plan, market=market))
    for result in results:
        print_fn(f"\n【{result.title}】")
        for row in result.rows:
            print_fn(f"  {row.label:<10} {row.text}")

    if not config.dry_run:
        orders, position = client.get_open_orders(symbol), client.get_position_qty(symbol)
        if orders or position != 0:
            print_fn(f"\n✗ 交易所上 {symbol} 還有掛單 {len(orders)} 張、持倉 {position},啟動會被擋下。請先到 Bybit 處理。")
            return 1

    expected = ("y", "yes") if config.dry_run else ("yes",)
    prompt = "\n輸入 y 確認啟動(dry-run): " if config.dry_run else "\n輸入 yes 確認以真實資金啟動(其他任何輸入取消): "
    if input_fn(prompt).strip().lower() not in expected:
        print_fn("已取消,沒有啟動。")
        return 0

    save_snapshot(config_path, results)
    try:
        info = (start_fn or daemon.start)(config_path)
    except daemon.DaemonError as e:
        print_fn(f"✗ 啟動失敗:{e}")
        return 1
    print_fn(f"已在背景啟動(PID {info.pid}),終端機輸出: {info.console_log}")
    print_fn(f"停止: {sys.executable} -m strategy_lab.live.daemon stop --config {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
