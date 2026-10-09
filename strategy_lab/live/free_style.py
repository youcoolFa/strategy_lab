"""free style:不用策略 YAML,使用者自己手動下單,strategy_lab 只負責「記帳」(2026-10-09)。

    .venv/bin/python -m strategy_lab.live.free_style start --symbol ETHUSDT
    (在 Bybit App / 網頁自己交易)
    .venv/bin/python -m strategy_lab.live.free_style stop --symbol ETHUSDT
    .venv/bin/python -m strategy_lab.live.free_style status

- start:只在 sl_run 寫一列(strategy_name = "free style"、開始時間、幣種;策略參數類欄位
  填 NA / NULL)就結束。中間不用有程式在跑,Mac 合蓋睡眠也不影響。
- stop:去 Bybit 把「開始 → 現在、這個幣種」的訂單和成交明細拉回來,寫進 sl_order
  (purpose = manual)/ sl_fill / sl_event,補上 sl_run 的結束時間與損益,發 🟣 Telegram 總結。
- 只讀 Bybit,不下單、不取消。

規則:
- 同一個幣同時只能有一段還沒結束的 run(另一段 free style 或 strategy_lab 實盤),否則單會混在一起。
- start 時這個幣要沒有持倉、沒有掛單(否則不知道原本的成本,損益會算錯)。
- stop 時還有持倉:這一輪不算進 sl_event(成交明細照樣寫),總結會提醒。
- Bybit 的訂單歷史 / 成交明細一次最多查 7 天,長的期間自動分段查。
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from loguru import logger
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from strategy_lab.engine.events import EventTracker, summarize
from strategy_lab.engine.runner import OrderRecord
from strategy_lab.storage.models import SlRun
from strategy_lab.storage.recorder import RunInfo, TradeRecorder

FREE_STYLE = "free style"
NA = "NA"
END_REASON = "free_style_stop"
MAX_QUERY_SPAN = timedelta(days=7)
HKT = ZoneInfo("Asia/Hong_Kong")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PENDING_DIR = PROJECT_ROOT / "logs" / "db_pending"

_OPEN = {"New", "PartiallyFilled", "Untriggered", "Created", "Active"}
_FILLED = {"Filled"}


class FreeStyleError(Exception):
    """不能開始 / 停止的原因(給人看的中文)。"""


@dataclass
class StopResult:
    run_id: str
    symbol: str
    started_at: datetime
    ended_at: datetime
    events: int
    orders: int
    gross_pnl: float
    fees: float
    funding: float
    net_pnl: float
    open_position: float


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

def start(db_url: Optional[str], client: Any, symbol: str, now: datetime, pending_dir: Path = PENDING_DIR) -> str:
    engine = _engine(db_url)
    try:
        running = _unfinished_runs(engine, symbol)
        if running:
            r = running[0]
            raise FreeStyleError(f"{symbol} 已經有一段還沒結束的紀錄({r.strategy_name},{_hkt(r.started_at)} 開始,"
                                 f"run_id {r.run_id});同一個幣同時只能有一段")
        position = client.get_position_qty(symbol)
        if position:
            raise FreeStyleError(f"{symbol} 現在有持倉 {position:g};free style 要從沒有持倉開始(不然算不出成本)")
        open_orders = client.get_open_orders(symbol)
        if open_orders:
            raise FreeStyleError(f"{symbol} 現在有 {len(open_orders)} 張掛單;free style 要從沒有掛單開始")
    finally:
        engine.dispose()

    recorder = TradeRecorder(db_url=db_url, pending_dir=pending_dir, fetch_executions=_never)
    if not recorder.db_available:
        raise FreeStyleError("交易資料庫連不上;free style 要靠資料庫記開始時間,請先確認 trading-postgres 有在跑")
    return recorder.start_run(RunInfo(
        strategy_name=FREE_STYLE, strategy_path=NA, strategy_yaml=NA, strategy_params={},
        config={"mode": FREE_STYLE, "symbol": symbol}, symbol=symbol, category="linear", direction=NA,
        origin_price=None, origin_source=None, qty=None, order_type=NA, loop=None, testnet=False,
        preflight=None, log_path=None, started_at=now,
    ))


def stop(db_url: Optional[str], client: Any, symbol: str, now: datetime, pending_dir: Path = PENDING_DIR) -> StopResult:
    engine = _engine(db_url)
    try:
        run = _running_free_style(engine, symbol)
        if run is None:
            raise FreeStyleError(f"{symbol} 沒有進行中的 free style(要先 start)")
        run_row = {c.name: getattr(run, c.name) for c in SlRun.__table__.columns}
    finally:
        engine.dispose()
    started = _utc(run_row["started_at"])
    run_row["started_at"] = started

    fetch_executions = _chunked(client.get_executions)
    orders = _orders_in(client, symbol, started, now)
    executions = [e for e in fetch_executions(symbol, started, now) if (e.get("execType") or "Trade") != "Funding"]
    events, order_event = _replay(orders, executions)

    recorder = TradeRecorder(db_url=db_url, pending_dir=pending_dir, fetch_executions=fetch_executions)
    recorder.run_id, recorder._run = run_row["run_id"], run_row
    for o in sorted(orders, key=lambda o: int(o["createdTime"])):
        _record_order(recorder, o, order_event[o["orderId"]])
    for event in events:
        recorder.record_event(event)
    recorder.end_run(now, END_REASON, summarize(events))

    costs = [recorder.event_costs(e.index) or (0.0, 0.0) for e in events]
    gross = sum(e.realized_pnl for e in events)
    fees, funding = sum(c[0] for c in costs), sum(c[1] for c in costs)
    return StopResult(
        run_id=run_row["run_id"], symbol=symbol, started_at=started, ended_at=now, events=len(events),
        orders=len(orders), gross_pnl=gross, fees=fees, funding=funding, net_pnl=gross - fees - funding,
        open_position=client.get_position_qty(symbol),
    )


def summary_message(r: StopResult) -> str:
    lines = [
        f"🏁 free style 結束|{r.symbol}",
        f"{_hkt(r.started_at)} → {_hkt(r.ended_at)}(共 {_duration(r.ended_at - r.started_at)})",
        f"完成 {r.events} 輪|訂單 {r.orders} 張",
        f"合計淨利 {r.net_pnl:+.2f} USDT(毛利 {r.gross_pnl:+.2f} − 手續費 {r.fees:.4f} − 資金費 {r.funding:.4f})",
    ]
    if r.open_position:
        lines.append(f"⚠️ 停止時還有持倉 {r.open_position:g}:這一輪沒算進損益(成交明細已寫進 sl_fill)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 內部
# ---------------------------------------------------------------------------

def _engine(db_url: Optional[str]):
    if not db_url:
        raise FreeStyleError("沒有交易資料庫設定(.env 的 TRADING_DB_URL);free style 要靠資料庫記開始時間")
    connect_args = {"connect_timeout": 5} if db_url.startswith("postgresql") else {}
    return create_engine(db_url, connect_args=connect_args, future=True)


def _unfinished_runs(engine, symbol: str) -> List[SlRun]:
    try:
        with Session(engine) as s:
            return list(s.scalars(select(SlRun).where(SlRun.symbol == symbol, SlRun.ended_at.is_(None))
                                  .where(SlRun.end_reason.is_(None))))
    except Exception as e:  # noqa: BLE001
        raise FreeStyleError(f"交易資料庫連不上:{e}") from None


def _running_free_style(engine, symbol: str) -> Optional[SlRun]:
    runs = [r for r in _unfinished_runs(engine, symbol) if r.strategy_name == FREE_STYLE]
    return max(runs, key=lambda r: _utc(r.started_at)) if runs else None


def _never(symbol: str, start: datetime, end: datetime) -> list:
    return []


def time_chunks(start: datetime, end: datetime, span: timedelta = MAX_QUERY_SPAN) -> List[Tuple[datetime, datetime]]:
    chunks, cursor = [], start
    while True:
        stop_at = min(cursor + span, end)
        chunks.append((cursor, stop_at))
        if stop_at >= end:
            return chunks
        cursor = stop_at


def _chunked(fetch: Callable[[str, datetime, datetime], list]) -> Callable[[str, datetime, datetime], list]:
    """把查詢切成每段最多 7 天;相鄰兩段的邊界會重複查到,用 id 去重。"""
    def fetch_all(symbol: str, start: datetime, end: datetime) -> list:
        seen: Dict[str, dict] = {}
        for s, e in time_chunks(start, end):
            for row in fetch(symbol, s, e):
                seen[row.get("execId") or row.get("orderId")] = row
        return list(seen.values())
    return fetch_all


def _orders_in(client: Any, symbol: str, start: datetime, end: datetime) -> List[dict]:
    by_id = {o["orderId"]: o for o in _chunked(client.list_order_history)(symbol, start, end)}
    for o in client.get_open_orders(symbol):  # 歷史清單不一定有還掛著的單
        by_id.setdefault(o["orderId"], o)
    return [o for o in by_id.values() if start <= _ms(o["createdTime"]) <= end]


def _replay(orders: List[dict], executions: List[dict]):
    """照時間重播成交,用跟實盤同一套 EventTracker 切出每一輪(部位 0 → 0)。
    每張單歸到「它第一筆成交時正在進行的那一輪」;沒成交的單歸到「下單時正在進行 / 下一個開始的那一輪」。"""
    tracker = EventTracker()
    events, order_event = [], {}
    for ex in sorted(executions, key=lambda e: (int(e["execTime"]), e["execId"])):
        sign = 1 if ex.get("side") == "Buy" else -1
        order_event.setdefault(ex.get("orderId"), tracker.completed + 1)
        event = tracker.on_fill(_ms(ex["execTime"]), sign * float(ex["execQty"]), float(ex["execPrice"]))
        if event is not None:
            events.append(event)
    for o in orders:
        if o["orderId"] not in order_event:
            created = _ms(o["createdTime"])
            order_event[o["orderId"]] = 1 + sum(1 for e in events if e.end_time < created)
    return events, order_event


def _record_order(recorder: TradeRecorder, o: dict, event_index: int) -> None:
    price = float(o.get("price") or 0)
    status = "open" if o["orderStatus"] in _OPEN else "closed" if o["orderStatus"] in _FILLED else "canceled"
    base = dict(
        order_id=o["orderId"], purpose="manual", event_index=event_index, side=o["side"],
        order_type=(o.get("orderType") or "").lower(), price=price if price > 0 and o.get("orderType") != "Market" else None,
        qty=float(o["qty"]), reduce_only=bool(o.get("reduceOnly")),
    )
    avg = float(o["avgPrice"]) if o.get("avgPrice") not in (None, "", "0") else None
    filled = float(o.get("cumExecQty") or 0)
    # 先記下單(created_at),再記最後狀態(updated_at)
    recorder.record_order(OrderRecord(**base, status="open", avg_price=None, filled_qty=0.0, time=_ms(o["createdTime"])))
    if status != "open":
        recorder.record_order(OrderRecord(**base, status=status, avg_price=avg, filled_qty=filled,
                                          time=_ms(o.get("updatedTime") or o["createdTime"])))


def _ms(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _hkt(dt: datetime) -> str:
    return _utc(dt).astimezone(HKT).strftime("%m-%d %H:%M")


def _duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    return (f"{days} 天 " if days else "") + f"{hours} 小時 {mins} 分"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _status(db_url: Optional[str]) -> str:
    engine = _engine(db_url)
    try:
        with Session(engine) as s:
            runs = list(s.scalars(select(SlRun).where(SlRun.strategy_name == FREE_STYLE, SlRun.ended_at.is_(None))))
    finally:
        engine.dispose()
    if not runs:
        return "沒有進行中的 free style"
    return "\n".join(f"進行中:{r.symbol},{_hkt(r.started_at)} HKT 開始(run_id {r.run_id})" for r in runs)


def _parse_since(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=HKT).astimezone(timezone.utc)


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="free style:手動交易的紀錄(start 記開始時間,stop 時從 Bybit 拉回訂單寫進資料庫)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_start = sub.add_parser("start", help="開始一段 free style")
    p_start.add_argument("--symbol", required=True, help="Bybit 交易對,例 ETHUSDT")
    p_start.add_argument("--since", help="補記:從這個時間開始(HKT,格式 \"2026-10-09 14:00\");不填 = 現在")
    p_stop = sub.add_parser("stop", help="結束,從 Bybit 拉回期間的訂單 / 成交寫進資料庫")
    p_stop.add_argument("--symbol", required=True)
    sub.add_parser("status", help="列出進行中的 free style")
    args = parser.parse_args(argv)

    db_url = os.getenv("TRADING_DB_URL")
    try:
        if args.command == "status":
            print(_status(db_url))
            return 0
        from strategy_lab.live.bybit_client import BybitClient  # 只讀;不下單
        client = BybitClient(testnet=False)
        symbol = args.symbol.upper()
        now = datetime.now(timezone.utc)
        if args.command == "start":
            when = _parse_since(args.since) if args.since else now
            run_id = start(db_url, client, symbol, when)
            print(f"✅ free style 開始:{symbol},{_hkt(when)} HKT(run_id {run_id})\n"
                  f"   自己去 Bybit 交易;結束時跑:.venv/bin/python -m strategy_lab.live.free_style stop --symbol {symbol}")
            return 0
        result = stop(db_url, client, symbol, now)
        text = summary_message(result)
        print(text)
        send_telegram(text)
        return 0
    except FreeStyleError as e:
        print(f"❌ {e}")
        return 1


def send_telegram(text: str) -> None:
    from strategy_lab.log.logger_setup import get_notifier

    notifier = get_notifier()
    if notifier.notify(text):
        notifier.flush()
    else:
        logger.info("Telegram 沒設定或發送失敗,總結只印在終端機")


if __name__ == "__main__":
    raise SystemExit(main())
