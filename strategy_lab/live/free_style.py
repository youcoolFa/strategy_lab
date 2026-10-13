"""free style:不用策略 YAML,使用者自己手動下單,strategy_lab 只負責「記帳」(2026-10-09)。

    .venv/bin/python -m strategy_lab.live.free_style start --symbol ETHUSDT
    (在 Bybit App / 網頁自己交易)
    .venv/bin/python -m strategy_lab.live.free_style stop --symbol ETHUSDT
    .venv/bin/python -m strategy_lab.live.free_style status
    .venv/bin/python -m strategy_lab.live.free_style backfill --symbol ETHUSDT --start "2026-10-09 14:00" --end "2026-10-09 18:00"

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
BACKFILL_END_REASON = "backfill"
EARLY_ORDER_LOOKBACK = timedelta(days=30)  # 補記時往前找「先掛好、期間內才成交」的訂單
MAX_BACKFILL_AGE = timedelta(days=730)  # Bybit 的訂單歷史 / 成交明細最多查 2 年
BACKFILL_DIRECTIONS = ("long", "short", "both")
MAX_NOTE_CHARS = 500
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
    account: str = "test1"  # 用哪個 Bybit 帳戶(2026-10-12)
    strategy_name: str = FREE_STYLE
    backfill: bool = False  # 補記過去一段(2026-10-13)


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

def _account_name(account: Optional[str]) -> str:
    from strategy_lab.live.accounts import DEFAULT_ACCOUNT

    return (account or DEFAULT_ACCOUNT).strip()


def start(db_url: Optional[str], client: Any, symbol: str, now: datetime, pending_dir: Path = PENDING_DIR,
          account: Optional[str] = None) -> str:
    """account:用哪個 Bybit 帳戶(None = test1);client 要是用那個帳戶的 key 建的(CLI / 頁面會處理)。"""
    account = _account_name(account)
    engine = _engine(db_url)
    try:
        running = _unfinished_runs(engine, symbol, account)
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
        preflight=None, log_path=None, started_at=now, account=account,
    ))


def stop(db_url: Optional[str], client: Any, symbol: str, now: datetime, pending_dir: Path = PENDING_DIR,
         account: Optional[str] = None) -> StopResult:
    account = _account_name(account)
    engine = _engine(db_url)
    try:
        run = _running_free_style(engine, symbol, account)
        if run is None:
            raise FreeStyleError(f"{symbol} 沒有進行中的 free style(要先 start)")
        run_row = {c.name: getattr(run, c.name) for c in SlRun.__table__.columns}
    finally:
        engine.dispose()
    started = _utc(run_row["started_at"])
    run_row["started_at"] = started

    fetch_executions = _chunked(client.get_executions)
    orders, executions = _fetch_window(client, symbol, started, now)
    recorder = TradeRecorder(db_url=db_url, pending_dir=pending_dir, fetch_executions=fetch_executions)
    recorder.run_id, recorder._run = run_row["run_id"], run_row
    return _write_window(recorder, symbol, orders, executions, started, now, END_REASON,
                         open_position=client.get_position_qty(symbol), account=run_row.get("account") or account)


def _fetch_window(client: Any, symbol: str, start: datetime, end: datetime) -> Tuple[List[dict], List[dict]]:
    """這段期間的訂單與成交(不含資金費;資金費由 recorder.event_costs 另外算)。"""
    executions = [e for e in _chunked(client.get_executions)(symbol, start, end)
                  if (e.get("execType") or "Trade") != "Funding"]
    orders = _orders_in(client, symbol, start, end)
    # 限價單常常先掛好、過一陣子才成交:建立時間早於開始、但在這段期間成交的訂單也要記(2026-10-14)
    missing = {e.get("orderId") for e in executions} - {o["orderId"] for o in orders} - {None, ""}
    if missing:
        earlier = _chunked(client.list_order_history)(symbol, start - EARLY_ORDER_LOOKBACK, start)
        orders += [o for o in earlier if o["orderId"] in missing]
    return orders, executions


def _write_window(recorder: TradeRecorder, symbol: str, orders: List[dict], executions: List[dict],
                  start: datetime, end: datetime, end_reason: str, open_position: float, account: str,
                  strategy_name: str = FREE_STYLE) -> StopResult:
    events, order_event = _replay(orders, executions)
    for o in sorted(orders, key=lambda o: int(o["createdTime"])):
        _record_order(recorder, o, order_event[o["orderId"]])
    for event in events:
        recorder.record_event(event)
    recorder.end_run(end, end_reason, summarize(events))

    costs = [recorder.event_costs(e.index) or (0.0, 0.0) for e in events]
    gross = sum(e.realized_pnl for e in events)
    fees, funding = sum(c[0] for c in costs), sum(c[1] for c in costs)
    return StopResult(
        run_id=recorder.run_id, symbol=symbol, started_at=start, ended_at=end, events=len(events),
        orders=len(orders), gross_pnl=gross, fees=fees, funding=funding, net_pnl=gross - fees - funding,
        open_position=open_position, account=account, strategy_name=strategy_name,
        backfill=end_reason == BACKFILL_END_REASON,
    )


# ---------------------------------------------------------------------------
# 補記過去一段(2026-10-13):通用,策略名稱可填(預設 free style)
# ---------------------------------------------------------------------------

@dataclass
class BackfillPreview:
    symbol: str
    account: str
    strategy_name: str
    start: datetime
    end: datetime
    orders: int
    events: int
    gross_pnl: float
    trade_fees: float  # 成交手續費(資金費寫入時才算)
    open_position: float  # 結束時的持倉(帶正負;0 = 已平)
    warnings: List[str]
    direction: str = NA  # 從成交判斷(每一對的第一筆:買 = long、賣 = short;兩種都有 = both);使用者填了就用使用者的


def preview_backfill(db_url: Optional[str], client: Any, symbol: str, start: datetime, end: datetime, now: datetime,
                     account: Optional[str] = None, strategy_name: str = FREE_STYLE,
                     expected_hold: Optional[timedelta] = None, direction: Optional[str] = None,
                     note: Optional[str] = None, target_price: Optional[float] = None,
                     stop_price: Optional[float] = None) -> BackfillPreview:
    """只讀:檢查時間、重疊、事後補填的值,從 Bybit 拉回那段期間的單與成交並算出每一輪。不寫 DB。"""
    user = _user_fields(expected_hold, direction, note, target_price, stop_price)
    _, _, preview = _prepare_backfill(db_url, client, symbol, start, end, now, account, strategy_name)
    preview.direction = user["direction"] or preview.direction
    _check_target_stop(preview.direction, user["target_price"], user["stop_price"])
    return preview


def _user_fields(expected_hold: Optional[timedelta], direction: Optional[str], note: Optional[str],
                 target_price: Optional[float] = None, stop_price: Optional[float] = None) -> Dict[str, Any]:
    """補記時使用者事後填的值(都選填):預計持倉時間、方向(不填 = 從成交判斷)、備註、目標價、停損價。
    回傳要寫進 sl_run 的欄位;user_filled 只列使用者自己填的。"""
    filled: List[str] = []
    out: Dict[str, Any] = {"expected_hold_seconds": None, "direction": None, "note": None,
                           "target_price": None, "stop_price": None}
    if expected_hold is not None:
        if expected_hold.total_seconds() <= 0:
            raise FreeStyleError("預計持倉時間要大於 0")
        out["expected_hold_seconds"] = round(expected_hold.total_seconds())
        filled.append("expected_hold")
    if direction:
        if direction not in BACKFILL_DIRECTIONS:
            raise FreeStyleError(f"方向只能是 {' / '.join(BACKFILL_DIRECTIONS)},收到 {direction!r}")
        out["direction"] = direction
        filled.append("direction")
    note = (note or "").strip()
    if note:
        if len(note) > MAX_NOTE_CHARS:
            raise FreeStyleError(f"備註最多 {MAX_NOTE_CHARS} 字")
        out["note"] = note
        filled.append("note")
    for key, label, value in (("target_price", "目標價", target_price), ("stop_price", "停損價", stop_price)):
        if value is not None:
            if value <= 0:
                raise FreeStyleError(f"{label}要大於 0")
            out[key] = float(value)
            filled.append(key)
    _check_target_stop(out["direction"], out["target_price"], out["stop_price"])
    out["user_filled"] = filled
    return out


def _check_target_stop(direction: Optional[str], target: Optional[float], stop: Optional[float]) -> None:
    if target is None or stop is None:
        return
    if direction == "long" and stop >= target:
        raise FreeStyleError(f"做多時停損價要低於目標價(停損 {stop:g}、目標 {target:g})")
    if direction == "short" and stop <= target:
        raise FreeStyleError(f"做空時停損價要高於目標價(停損 {stop:g}、目標 {target:g})")


def _direction_of(executions: List[dict]) -> str:
    """每一對交易(持倉 0 → 0)的第一筆:買 = long、賣 = short;兩種都有 = both;沒有成交 = NA。"""
    sides, pos = set(), 0.0
    for e in sorted(executions, key=lambda e: (int(e["execTime"]), e["execId"])):
        qty = float(e["execQty"]) * (1 if e.get("side") == "Buy" else -1)
        after = pos + qty
        if abs(pos) < 1e-9:
            sides.add("long" if qty > 0 else "short")
        elif pos * after < 0 and abs(after) > 1e-9:  # 穿過 0 = 反向開倉(小數誤差的 −4e-16 不算)
            sides.add("long" if qty > 0 else "short")
        pos = 0.0 if abs(after) < 1e-9 else after
    return sides.pop() if len(sides) == 1 else "both" if sides else NA


def parse_hold(text: Optional[str]) -> Optional[timedelta]:
    """預計持倉時間的簡寫:90m / 4h / 1.5d;空白 = 不填。"""
    text = (text or "").strip().lower()
    if not text:
        return None
    units = {"m": "minutes", "h": "hours", "d": "days"}
    try:
        value, unit = float(text[:-1]), units[text[-1]]
    except (ValueError, KeyError):
        raise FreeStyleError(f"預計持倉時間要寫成 90m / 4h / 1.5d,收到 {text!r}") from None
    return timedelta(**{unit: value})


def backfill(db_url: Optional[str], client: Any, symbol: str, start: datetime, end: datetime, now: datetime,
             account: Optional[str] = None, strategy_name: str = FREE_STYLE,
             pending_dir: Path = PENDING_DIR, expected_hold: Optional[timedelta] = None,
             direction: Optional[str] = None, note: Optional[str] = None, target_price: Optional[float] = None,
             stop_price: Optional[float] = None) -> StopResult:
    """重新檢查、重新拉資料後一次寫進 DB:sl_run(started/ended = 選的時間,end_reason = backfill)、
    sl_order / sl_fill / sl_event。"""
    user = _user_fields(expected_hold, direction, note, target_price, stop_price)
    orders, executions, p = _prepare_backfill(db_url, client, symbol, start, end, now, account, strategy_name)
    direction = user["direction"] or p.direction
    _check_target_stop(direction, user["target_price"], user["stop_price"])
    recorder = TradeRecorder(db_url=db_url, pending_dir=pending_dir, fetch_executions=_chunked(client.get_executions))
    if not recorder.db_available:
        raise FreeStyleError("交易資料庫連不上;請先確認 trading-postgres 有在跑")
    recorder.start_run(RunInfo(
        strategy_name=p.strategy_name, strategy_path=NA, strategy_yaml=NA, strategy_params={},
        config={"mode": "backfill", "symbol": symbol, "backfilled_at": now.isoformat(),
                "user_filled": user["user_filled"]},  # 這些欄位是補記時使用者事後填的
        symbol=symbol, category="linear", direction=direction, origin_price=None, origin_source=None,
        qty=None, order_type=NA, loop=None, testnet=False, preflight=None, log_path=None, started_at=start,
        account=p.account, expected_hold_seconds=user["expected_hold_seconds"], note=user["note"],
        target_price=user["target_price"], stop_price=user["stop_price"],
    ))
    return _write_window(recorder, symbol, orders, executions, start, end, BACKFILL_END_REASON,
                         open_position=p.open_position, account=p.account, strategy_name=p.strategy_name)


def _prepare_backfill(db_url, client, symbol, start, end, now, account, strategy_name):
    account = _account_name(account)
    name = (strategy_name or "").strip()
    if not name:
        raise FreeStyleError("請填策略名稱(例:free style)")
    start, end, now = _utc(start), _utc(end), _utc(now)
    if start >= end:
        raise FreeStyleError("開始要早於結束")
    if end > now:
        raise FreeStyleError("結束不能晚於現在(補記只做已經結束的期間)")
    if now - start > MAX_BACKFILL_AGE:
        raise FreeStyleError("Bybit 的訂單歷史最多只能查 2 年")
    _check_overlap(db_url, symbol, account, start, end, now)

    orders, executions = _fetch_window(client, symbol, start, end)
    if not orders and not executions:
        raise FreeStyleError(f"{_hkt(start)} → {_hkt(end)} {symbol} 沒有交易(帳戶 {account})")
    first = min(executions, key=lambda e: (int(e["execTime"]), e["execId"])) if executions else None
    if first is not None and float(first.get("closedSize") or 0) > 0:
        raise FreeStyleError(f"開始時已經有持倉({_hkt(_ms(first['execTime']))} 的第一筆成交是平倉),"
                             "算不出成本;請把開始時間往前移到建倉之前")
    events, _ = _replay(orders, executions)
    position = round(sum((1 if e.get("side") == "Buy" else -1) * float(e["execQty"]) for e in executions), 10)
    warnings = []
    if position:
        warnings.append(f"結束時還有持倉 {position:g}:最後這一輪沒算進損益(成交明細照樣寫進 sl_fill)")
    preview = BackfillPreview(
        symbol=symbol, account=account, strategy_name=name, start=start, end=end, orders=len(orders),
        events=len(events), gross_pnl=sum(e.realized_pnl for e in events),
        trade_fees=sum(float(e.get("execFee") or 0) for e in executions), open_position=position, warnings=warnings,
        direction=_direction_of(executions))
    return orders, executions, preview


@dataclass(frozen=True)
class Segment:
    """DB 裡一筆紀錄的時段(還沒結束的算到現在)。"""
    run_id: str
    strategy_name: str
    start: datetime
    end: datetime
    running: bool

    def label(self) -> str:
        return f"{self.strategy_name} {_hkt(self.start)} → {'進行中' if self.running else _hkt(self.end)}"


def existing_segments(db_url: Optional[str], symbol: str, account: Optional[str], now: datetime) -> List[Segment]:
    """同帳戶、同幣種已有的紀錄時段(不分策略:成交是靠帳戶 + 幣種歸到紀錄的),舊的在前。只查 DB。"""
    account = _account_name(account)
    engine = _engine(db_url)
    try:
        with Session(engine) as s:
            runs = list(s.scalars(select(SlRun).where(SlRun.symbol == symbol)))
    except Exception as e:  # noqa: BLE001
        raise FreeStyleError(f"交易資料庫連不上:{e}") from None
    finally:
        engine.dispose()
    segs = [Segment(r.run_id, r.strategy_name, _utc(r.started_at), _utc(r.ended_at) if r.ended_at else _utc(now),
                    r.ended_at is None) for r in runs if _account_name(r.account).upper() == account.upper()]
    return sorted(segs, key=lambda g: g.start)


def overlap_marks(segments: List[Segment], start: datetime, end: datetime) -> Dict[str, Any]:
    """開始落在哪一段、結束落在哪一段、整段被包住的有哪些。剛好接上(結束 = 下一段開始)不算重疊。"""
    start, end = _utc(start), _utc(end)
    at_start = next((g for g in segments if g.start <= start < g.end), None)
    at_end = next((g for g in segments if g.start < end <= g.end), None)
    inside = [g for g in segments if start <= g.start and g.end <= end and g not in (at_start, at_end)]
    return {"start": at_start, "end": at_end, "inside": inside}


def _check_overlap(db_url, symbol, account, start, end, now) -> None:
    """同帳戶、同幣種、時間重疊的紀錄 → 成交會被算兩次。還沒結束的紀錄算到現在。"""
    for g in existing_segments(db_url, symbol, account, now):
        if g.start < end and g.end > start:
            raise FreeStyleError(f"跟已有的紀錄重疊:{g.label()}(run_id {g.run_id});同一段成交會被算兩次")


def summary_message(r: StopResult) -> str:
    lines = [
        (f"📝 {r.strategy_name} 補記" if r.backfill else "🏁 free style 結束") + f"|{r.symbol}|帳戶 {r.account}",
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


def _unfinished_runs(engine, symbol: str, account: str) -> List[SlRun]:
    """這個帳戶、這個幣還沒結束的 run(帳戶不分大小寫;舊紀錄沒有帳戶 = test1)。"""
    try:
        with Session(engine) as s:
            runs = list(s.scalars(select(SlRun).where(SlRun.symbol == symbol, SlRun.ended_at.is_(None))
                                  .where(SlRun.end_reason.is_(None))))
    except Exception as e:  # noqa: BLE001
        raise FreeStyleError(f"交易資料庫連不上:{e}") from None
    return [r for r in runs if _account_name(r.account).upper() == account.upper()]


def _running_free_style(engine, symbol: str, account: str) -> Optional[SlRun]:
    runs = [r for r in _unfinished_runs(engine, symbol, account) if r.strategy_name == FREE_STYLE]
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
    p_start.add_argument("--account", help="Bybit 帳戶名稱(.env 的 ACCOUNT = ...);不填 = test1")
    p_start.add_argument("--since", help="補記:從這個時間開始(HKT,格式 \"2026-10-09 14:00\");不填 = 現在")
    p_stop = sub.add_parser("stop", help="結束,從 Bybit 拉回期間的訂單 / 成交寫進資料庫")
    p_stop.add_argument("--symbol", required=True)
    p_stop.add_argument("--account", help="Bybit 帳戶名稱;不填 = test1")
    p_back = sub.add_parser("backfill", help="補記過去一段(開始、結束都在過去),從 Bybit 拉回那段期間的單寫進資料庫")
    p_back.add_argument("--symbol", required=True)
    p_back.add_argument("--start", required=True, help="HKT,格式 \"2026-10-09 14:00\"")
    p_back.add_argument("--end", required=True, help="HKT,格式 \"2026-10-09 18:00\"")
    p_back.add_argument("--name", default=FREE_STYLE, help="策略名稱(預設 free style)")
    p_back.add_argument("--account", help="Bybit 帳戶名稱;不填 = test1")
    p_back.add_argument("--hold", help="選填:預計持倉時間,例 90m / 4h / 1.5d")
    p_back.add_argument("--direction", choices=BACKFILL_DIRECTIONS, help="選填:方向(不填 = 從成交判斷)")
    p_back.add_argument("--note", help="選填:備註")
    p_back.add_argument("--target", type=float, help="選填:目標價(沒掛單的止盈)")
    p_back.add_argument("--stop", type=float, help="選填:停損價(心理停損)")
    sub.add_parser("status", help="列出進行中的 free style")
    args = parser.parse_args(argv)

    db_url = os.getenv("TRADING_DB_URL")
    try:
        if args.command == "status":
            print(_status(db_url))
            return 0
        from strategy_lab.live import accounts
        from strategy_lab.live.bybit_client import BybitClient  # 只讀;不下單
        try:
            account = accounts.resolve(args.account)
        except accounts.AccountError as e:
            raise FreeStyleError(str(e)) from e
        client = BybitClient(api_key=account.api_key, api_secret=account.api_secret, testnet=False)
        from strategy_lab.ui.actions import perp_symbol

        symbol = perp_symbol(args.symbol)  # 只填幣名也可以:eth → ETHUSDT
        now = datetime.now(timezone.utc)
        if args.command == "backfill":
            result = backfill(db_url, client, symbol, _parse_since(args.start), _parse_since(args.end), now,
                              account=args.account, strategy_name=args.name, expected_hold=parse_hold(args.hold),
                              direction=args.direction, note=args.note, target_price=args.target,
                              stop_price=args.stop)
            text = summary_message(result)
            print(text)
            send_telegram(text)
            return 0
        if args.command == "start":
            when = _parse_since(args.since) if args.since else now
            run_id = start(db_url, client, symbol, when, account=args.account)
            acct_opt = f" --account {account.name}" if args.account else ""
            print(f"✅ free style 開始:{symbol},{account},{_hkt(when)} HKT(run_id {run_id})\n"
                  f"   自己去 Bybit 交易;結束時跑:.venv/bin/python -m strategy_lab.live.free_style stop --symbol {symbol}{acct_opt}")
            return 0
        result = stop(db_url, client, symbol, now, account=args.account)
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
