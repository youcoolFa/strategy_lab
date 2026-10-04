"""把一次實盤執行的 run / order / fill / event 寫進資料庫。

- 寫入一律 upsert(自然主鍵),重送不會重複。
- 手續費與資金費以 Bybit 成交明細為準:每個 event 完成時同步一次該 event
  期間的成交明細,收尾時再整段同步一次並重算(成交回報可能晚一點才出現)。
- 只收這次執行下的單的成交;資金費依時間歸到對應的 event。
- 資料庫連不上不影響交易:啟動時連不上、或寫入中途失敗,之後這次執行的
  紀錄都改寫 `<pending_dir>/<run_id>.jsonl`,不再嘗試連線(避免拖慢交易
  迴圈),之後用 storage/backfill.py 補進資料庫。
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.types import TIMESTAMP

from strategy_lab.engine.events import Event, summarize
from strategy_lab.engine.runner import OrderRecord
from strategy_lab.storage.models import MODELS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SECRET = re.compile(r"(key|secret|password|token)", re.IGNORECASE)
_WINDOW_PAD = timedelta(minutes=1)


@dataclass
class RunInfo:
    strategy_name: str
    strategy_path: str
    strategy_yaml: str
    strategy_params: Dict[str, Any]
    config: Dict[str, Any]
    symbol: str
    category: str
    direction: str
    origin_price: Optional[float]
    origin_source: Optional[str]
    qty: Optional[float]
    order_type: str
    loop: Optional[int]
    testnet: bool
    preflight: Optional[Dict[str, Any]]
    log_path: Optional[str]
    started_at: datetime


def _strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_secrets(v) for k, v in value.items() if not _SECRET.search(str(k))}
    return value


def _git_commit() -> Optional[str]:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
        return f"{sha}{'-dirty' if dirty else ''}" if sha else None
    except Exception:
        return None


def encode_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()}


def decode_row(table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    model = MODELS[table]
    out = dict(row)
    for column in model.__table__.columns:
        if isinstance(column.type, TIMESTAMP) and isinstance(out.get(column.name), str):
            out[column.name] = datetime.fromisoformat(out[column.name])
    return out


def _ms_to_dt(ms: Any) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


class TradeRecorder:
    def __init__(
        self,
        db_url: Optional[str],
        pending_dir: Path,
        fetch_executions: Callable[[str, datetime, datetime], List[dict]],
        connect_timeout: int = 5,
    ) -> None:
        self.pending_dir = Path(pending_dir)
        self.fetch_executions = fetch_executions
        self.db_available = False
        self._engine = None
        if db_url:
            try:
                connect_args = {"connect_timeout": connect_timeout} if db_url.startswith("postgresql") else {}
                engine = create_engine(db_url, connect_args=connect_args, future=True)
                with engine.connect():
                    pass
                self._engine, self.db_available = engine, True
            except Exception as e:
                logger.warning(f"交易資料庫連不上,這次執行的紀錄改寫本機檔案(之後用 backfill 補):{e}")
        else:
            logger.warning("沒有設定 TRADING_DB_URL,這次執行的交易紀錄只寫本機檔案(之後可用 backfill 補進資料庫)")

        self.run_id: Optional[str] = None
        self._run: Dict[str, Any] = {}
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._events: Dict[int, Event] = {}
        self._fills: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    def start_run(self, info: RunInfo) -> str:
        self.run_id = str(uuid.uuid4())
        row = asdict(info)
        row["config"] = _strip_secrets(row["config"])
        row.update(
            run_id=self.run_id,
            python_executable=sys.executable,
            python_version=platform.python_version(),
            git_commit=_git_commit(),
        )
        self._run = row
        self._write("sl_run", row)
        return self.run_id

    def record_order(self, rec: OrderRecord) -> None:
        prev = self._orders.get(rec.order_id)
        row = {
            "order_id": rec.order_id, "run_id": self.run_id, "event_index": rec.event_index,
            "purpose": rec.purpose, "side": rec.side, "order_type": rec.order_type, "price": rec.price,
            "qty": rec.qty, "reduce_only": rec.reduce_only, "status": rec.status, "avg_price": rec.avg_price,
            "filled_qty": rec.filled_qty, "created_at": prev["created_at"] if prev else rec.time, "updated_at": rec.time,
        }
        self._orders[rec.order_id] = row
        self._write("sl_order", row)

    def record_event(self, event: Event) -> None:
        self._events[event.index] = event
        self._sync_fills(event.start_time - _WINDOW_PAD, event.end_time + _WINDOW_PAD)
        self._write("sl_event", self._event_row(event))

    def end_run(self, now: datetime, reason: str, summary: Any = None) -> None:
        if self.run_id is None:
            return
        self._sync_fills(self._run["started_at"] - _WINDOW_PAD, now + _WINDOW_PAD)
        event_rows = [self._event_row(e) for e in sorted(self._events.values(), key=lambda e: e.index)]
        for row in event_rows:
            self._write("sl_event", row)
        stats = summarize(list(self._events.values()))
        self._run.update(
            ended_at=now, end_reason=reason, events_count=stats.count, gross_pnl=stats.total_pnl,
            net_pnl=sum(r["net_pnl"] for r in event_rows), max_drawdown=stats.max_drawdown,
        )
        self._write("sl_run", self._run)

    # ------------------------------------------------------------------
    def _sync_fills(self, start: datetime, end: datetime) -> None:
        try:
            executions = self.fetch_executions(self._run["symbol"], start, end)
        except Exception as e:
            logger.warning(f"查 Bybit 成交明細失敗,手續費/資金費稍後收尾時再補:{e}")
            return
        for ex in executions:
            exec_type = ex.get("execType") or "Trade"
            order_id = ex.get("orderId")
            exec_time = _ms_to_dt(ex["execTime"])
            if order_id in self._orders:
                event_index = self._orders[order_id]["event_index"]
            elif exec_type == "Funding":
                event_index = self._event_at(exec_time)
            else:
                continue  # 不是這次執行下的單
            row = {
                "exec_id": ex["execId"], "run_id": self.run_id, "order_id": order_id, "event_index": event_index,
                "exec_type": exec_type, "side": ex.get("side"), "price": float(ex.get("execPrice") or 0),
                "qty": float(ex.get("execQty") or 0), "fee": float(ex.get("execFee") or 0),
                "is_maker": ex.get("isMaker"), "exec_time": exec_time,
            }
            if self._fills.get(row["exec_id"]) != row:
                self._fills[row["exec_id"]] = row
                self._write("sl_fill", row)

    def _event_at(self, when: datetime) -> Optional[int]:
        for e in self._events.values():
            if e.start_time - _WINDOW_PAD <= when <= e.end_time + _WINDOW_PAD:
                return e.index
        return None

    def _event_row(self, event: Event) -> Dict[str, Any]:
        mine = [f for f in self._fills.values() if f["event_index"] == event.index]
        fees = sum(f["fee"] for f in mine if f["exec_type"] != "Funding")
        funding = sum(f["fee"] for f in mine if f["exec_type"] == "Funding")
        return {
            "run_id": self.run_id, "event_index": event.index, "direction": event.direction,
            "start_time": event.start_time, "end_time": event.end_time, "fills": event.fills,
            "max_position": event.max_position, "avg_entry": event.avg_entry, "avg_exit": event.avg_exit,
            "realized_pnl": event.realized_pnl, "fees": fees, "funding": funding,
            "net_pnl": event.realized_pnl - fees - funding, "max_drawdown": event.max_drawdown, "forced": event.forced,
        }

    def _write(self, table: str, row: Dict[str, Any]) -> None:
        if self.db_available:
            try:
                with Session(self._engine) as session:
                    session.merge(MODELS[table](**row))
                    session.commit()
                return
            except Exception as e:
                self.db_available = False
                logger.warning(f"寫入交易資料庫失敗,這次執行剩下的紀錄改寫本機檔案:{e}")
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        with open(self.pending_dir / f"{self.run_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"table": table, "data": encode_row(row)}, ensure_ascii=False) + "\n")
