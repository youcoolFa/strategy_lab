"""每日對帳(2026-10-13):Bybit 上的成交 vs trading DB。只讀 Bybit,不下單、不取消。

    .venv/bin/python -m strategy_lab.live.reconcile            # 每天 08:00 HKT 由 launchd 跑(scripts/)
    .venv/bin/python -m strategy_lab.live.reconcile --days 3   # 手動:不管上次對帳,往回查 3 天

每次:
1. 先把 logs/db_pending/*.jsonl(DB 斷線時存在本機的紀錄)補進 DB —— 策略交易漏記最常見的原因
2. 每個帳戶,從 Bybit 拉「上次對帳 → 現在」的所有成交(不分幣種;第一次往回 7 天)
3. 每筆成交用 execId 去 sl_fill 查:
   - 有 → 正常
   - 沒有,但落在同帳戶、同幣種某筆紀錄的時間裡 → 情況 1:自動補進那筆紀錄(sl_fill)
   - 沒有,也不在任何紀錄裡(資金費不算)→ 情況 2:全手動交易沒記,不自動補(不知道策略與參數),
     列進「未記錄的交易」,發系統警報請使用者到頁面「補記」分頁補
4. 情況 1 / 2、帳戶查詢失敗 → Telegram 系統警報(有聲音);警告過的成交記在狀態檔,不重發

限制:情況 1 只補成交明細,每一輪的損益(sl_event)不重算;通知會註明。
狀態檔 logs/reconcile_state.json:每個帳戶上次對帳到哪、還沒補的「未記錄的交易」、警告過的 execId。
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from strategy_lab.live.free_style import PENDING_DIR, PROJECT_ROOT, _account_name, _chunked, _hkt, _ms, _utc
from strategy_lab.storage.models import SlEvent, SlFill, SlRun

STATE_PATH = PROJECT_ROOT / "logs" / "reconcile_state.json"
FIRST_LOOKBACK = timedelta(days=7)
ALERTED_KEEP = timedelta(days=30)
SESSION_GAP = timedelta(hours=6)  # 未記錄的成交:同一個幣兩筆間隔超過這麼久就分成新的一組(各自補記)


@dataclass
class Repaired:
    """情況 1:補進既有紀錄的成交。"""
    account: str
    symbol: str
    run_id: str
    strategy_name: str
    fills: int


@dataclass
class Unrecorded:
    """情況 2:不在任何紀錄裡的成交(同帳戶、同幣種一組)。"""
    account: str
    symbol: str
    first: datetime
    last: datetime
    count: int
    exec_ids: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["first"], d["last"] = self.first.isoformat(), self.last.isoformat()
        return d

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Unrecorded":
        return cls(**{**d, "first": datetime.fromisoformat(d["first"]), "last": datetime.fromisoformat(d["last"])})


@dataclass
class Report:
    account: str
    start: datetime
    end: datetime
    checked: int
    repaired: List[Repaired]
    unrecorded: List[Unrecorded]


# ---------------------------------------------------------------------------
# 核心
# ---------------------------------------------------------------------------

def reconcile_account(db_url: str, client: Any, account: str, start: datetime, end: datetime) -> Report:
    executions = _chunked(client.get_executions)(None, start, end)
    engine = create_engine(db_url, future=True)
    try:
        with Session(engine) as s:
            known = _known_exec_ids(s, [e["execId"] for e in executions])
            runs = [r for r in s.scalars(select(SlRun)) if _account_name(r.account).upper() == account.upper()]
            missing = [e for e in executions if e["execId"] not in known]
            repaired: Dict[str, Repaired] = {}
            loose: Dict[str, List[dict]] = {}
            for e in sorted(missing, key=lambda e: (int(e["execTime"]), e["execId"])):
                when = _ms(e["execTime"])
                run = _run_at(runs, e["symbol"], when, end)
                if run is not None:
                    s.add(_fill_row(s, run.run_id, e, when))
                    rep = repaired.setdefault(run.run_id, Repaired(account, e["symbol"], run.run_id, run.strategy_name, 0))
                    rep.fills += 1
                elif (e.get("execType") or "Trade") == "Trade":
                    loose.setdefault(e["symbol"], []).append(e)
            s.commit()
    finally:
        engine.dispose()
    unrecorded = [Unrecorded(account, sym, _ms(g[0]["execTime"]), _ms(g[-1]["execTime"]), len(g),
                             [r["execId"] for r in g]) for sym, rows in sorted(loose.items()) for g in _sessions(rows)]
    return Report(account, start, end, len(executions), list(repaired.values()), unrecorded)


def _sessions(rows: List[dict]) -> List[List[dict]]:
    groups: List[List[dict]] = []
    for r in rows:  # 已照時間排好
        if groups and _ms(r["execTime"]) - _ms(groups[-1][-1]["execTime"]) <= SESSION_GAP:
            groups[-1].append(r)
        else:
            groups.append([r])
    return groups


def _known_exec_ids(session: Session, ids: List[str]) -> set:
    known: set = set()
    for i in range(0, len(ids), 500):
        known |= set(session.scalars(select(SlFill.exec_id).where(SlFill.exec_id.in_(ids[i:i + 500]))))
    return known


def _run_at(runs: List[SlRun], symbol: str, when: datetime, now: datetime) -> Optional[SlRun]:
    for r in runs:
        r_end = _utc(r.ended_at) if r.ended_at else _utc(now)
        if r.symbol == symbol and _utc(r.started_at) <= when <= r_end:
            return r
    return None


def _fill_row(session: Session, run_id: str, e: dict, when: datetime) -> SlFill:
    event = next((ev.event_index for ev in session.scalars(select(SlEvent).where(SlEvent.run_id == run_id))
                  if _utc(ev.start_time) <= when <= _utc(ev.end_time)), None)
    return SlFill(exec_id=e["execId"], run_id=run_id, order_id=e.get("orderId") or None, event_index=event,
                  exec_type=e.get("execType") or "Trade", side=e.get("side"), price=float(e.get("execPrice") or 0),
                  qty=float(e.get("execQty") or 0), fee=float(e.get("execFee") or 0),
                  is_maker=bool(e.get("isMaker")), exec_time=when)


# ---------------------------------------------------------------------------
# 每日執行
# ---------------------------------------------------------------------------

def run_daily(db_url: str, now: datetime, state_path: Path = STATE_PATH, pending_dir: Path = PENDING_DIR,
              lookback: Optional[timedelta] = None) -> List[Report]:
    from strategy_lab.live import accounts

    state = _load_state(state_path)
    messages: List[str] = []
    replayed = replay_pending(db_url, pending_dir)
    if replayed:
        messages.append(f"🔧 對帳:已把本機暫存(DB 斷線時)的 {replayed} 筆紀錄補進資料庫")

    reports: List[Report] = []
    names = accounts.list_accounts()
    if accounts.DEFAULT_ACCOUNT.upper() not in [n.upper() for n in names]:
        names.insert(0, accounts.DEFAULT_ACCOUNT)  # test1 只要有原本的 BYBIT_API_KEY 就對帳(.env 沒寫標題也一樣)
    for name in names:
        try:
            account = accounts.resolve(name)
        except accounts.AccountError as e:
            if name == accounts.DEFAULT_ACCOUNT:
                continue  # 沒有任何 test1 的 key:這台機器沒用 test1,不算錯
            messages.append(f"❌ 對帳:帳戶 {name} 查詢失敗:{e}")
            continue
        try:
            client = make_client(account)
            last = state["accounts"].get(account.name, {}).get("last_end")
            start = now - lookback if lookback else (datetime.fromisoformat(last) if last else now - FIRST_LOOKBACK)
            report = reconcile_account(db_url, client, account.name, start, now)
        except Exception as e:  # noqa: BLE001  一個帳戶失敗不影響其他帳戶
            messages.append(f"❌ 對帳:帳戶 {name} 查詢失敗:{e}")
            continue
        reports.append(report)
        state["accounts"][account.name] = {"last_end": now.isoformat()}
        for rep in report.repaired:
            messages.append(f"🔧 對帳:帳戶 {rep.account} {rep.symbol} 已自動補回 {rep.fills} 筆成交到 {rep.strategy_name}"
                            f"(run_id {rep.run_id[:8]});這筆紀錄的每輪損益可能不完整")

    # 未記錄的交易:之前的 + 這次的,去掉已經補記(被紀錄時段涵蓋)的
    pending = [Unrecorded.from_json(d) for d in state["unrecorded"]]
    seen_ids = {i for u in pending for i in u.exec_ids}
    pending += [u for r in reports for u in r.unrecorded if not set(u.exec_ids) <= seen_ids]
    pending = [u for u in pending if not _covered(db_url, u, now)]
    alerted = {k: v for k, v in state["alerted"].items() if now - datetime.fromisoformat(v) < ALERTED_KEEP}
    new = [u for u in pending if not set(u.exec_ids) <= set(alerted)]
    if new:
        lines = [f"• 帳戶 {u.account} {u.symbol}:{_hkt(u.first)} → {_hkt(u.last)} HKT,{u.count} 筆成交" for u in new]
        messages.append("⚠️ 對帳:有成交沒有任何紀錄(全手動交易沒記?)\n" + "\n".join(lines)
                        + "\n→ 到 strategy_lab 頁面「補記」分頁補(總覽的「未記錄的交易」可一鍵帶入)")
        for u in new:
            for i in u.exec_ids:
                alerted[i] = now.isoformat()
    state["unrecorded"] = [u.to_json() for u in pending]
    state["alerted"] = alerted
    state["last_run"] = now.isoformat()
    _save_state(state_path, state)

    for m in messages:
        send_alert(m)
    logger.info("對帳完成:{} 個帳戶,未記錄的交易 {} 組,通知 {} 則", len(reports), len(pending), len(messages))
    return reports


def _covered(db_url: str, u: Unrecorded, now: datetime) -> bool:
    """這組成交已經被某筆紀錄涵蓋(使用者補記了)。"""
    engine = create_engine(db_url, future=True)
    try:
        with Session(engine) as s:
            runs = [r for r in s.scalars(select(SlRun).where(SlRun.symbol == u.symbol))
                    if _account_name(r.account).upper() == u.account.upper()]
    finally:
        engine.dispose()
    return _run_at(runs, u.symbol, u.first, now) is not None and _run_at(runs, u.symbol, u.last, now) is not None


def load_unrecorded(state_path: Path = STATE_PATH) -> List[Unrecorded]:
    """給頁面:上次對帳留下、還沒補的「未記錄的交易」。"""
    return [Unrecorded.from_json(d) for d in _load_state(state_path)["unrecorded"]]


def last_run(state_path: Path = STATE_PATH) -> Optional[datetime]:
    value = _load_state(state_path).get("last_run")
    return datetime.fromisoformat(value) if value else None


def _load_state(path: Path) -> Dict[str, Any]:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.setdefault("accounts", {})
    state.setdefault("unrecorded", [])
    state.setdefault("alerted", {})
    return state


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# 外部依賴(測試換成假的)
# ---------------------------------------------------------------------------

def replay_pending(db_url: str, pending_dir: Path) -> int:
    from strategy_lab.storage.backfill import backfill

    return backfill(db_url, Path(pending_dir)) if Path(pending_dir).exists() else 0


def make_client(account) -> Any:
    from strategy_lab.live.bybit_client import BybitClient

    return BybitClient(api_key=account.api_key, api_secret=account.api_secret, testnet=False)


def send_alert(text: str) -> None:
    """系統警報 bot(有聲音);沒設定就送原本的 bot。"""
    from strategy_lab.log.logger_setup import get_notifier

    notifier = get_notifier()
    if notifier.notify(text, route="system"):
        notifier.flush()
    else:
        logger.warning("Telegram 沒設定或發送失敗:{}", text)


def main(argv: Optional[List[str]] = None) -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="每日對帳:Bybit 成交 vs trading DB(只讀 Bybit)")
    parser.add_argument("--days", type=float, help="不管上次對帳,往回查幾天")
    args = parser.parse_args(argv)
    db_url = os.getenv("TRADING_DB_URL")
    if not db_url:
        print("沒有 TRADING_DB_URL(.env)")
        return 1
    reports = run_daily(db_url, datetime.now(timezone.utc),
                        lookback=timedelta(days=args.days) if args.days else None)
    for r in reports:
        print(f"帳戶 {r.account}:{_hkt(r.start)} → {_hkt(r.end)} 查了 {r.checked} 筆;"
              f"自動補回 {sum(x.fills for x in r.repaired)} 筆;未記錄 {sum(u.count for u in r.unrecorded)} 筆")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
