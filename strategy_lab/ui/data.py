"""Streamlit 介面的資料層(2026-10-09)。全部只讀:不下單、不取消、不動 daemon。
畫面(app.py)只負責顯示;這裡不 import streamlit,可以單獨測試。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from strategy_lab.live.accounts import DEFAULT_ACCOUNT
from strategy_lab.live.daemon import _read_pid, _tail, is_alive
from strategy_lab.storage.models import SlEvent, SlOrder, SlRun

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 每小時一定有一則 ⚫ 狀態回報;運作中卻超過這麼久沒寫 log → Mac 睡著或程式卡住
STALE_AFTER = timedelta(minutes=70)
FREE_STYLE = "free style"


# ---------------------------------------------------------------------------
# daemon(背景執行的實盤程式)
# ---------------------------------------------------------------------------

@dataclass
class DaemonRow:
    config: str  # 設定檔名(不含 .yaml)
    symbol: Optional[str]
    pid: Optional[int]
    alive: bool
    console_log: Path
    last_log_at: Optional[datetime]
    account: str = DEFAULT_ACCOUNT  # 設定檔的 account:(沒寫 = test1)


def list_daemons(project_root: Path = PROJECT_ROOT) -> List[DaemonRow]:
    """每一份 live_*.yaml 執行設定(範本 *.example.yaml 不算)目前的狀態。"""
    rows = []
    for config in sorted(project_root.glob("live_*.yaml")):
        if config.name.endswith(".example.yaml"):
            continue
        pid = _read_pid(project_root / "run" / f"{config.stem}.pid")
        logs = [project_root / "logs" / f"{config.stem}.console.log",
                *(project_root / "logs").glob(f"{config.stem}_*.log*")]
        times = [datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc) for p in logs if p.exists()]
        rows.append(DaemonRow(
            config=config.stem, symbol=_symbol_of(config), pid=pid, alive=bool(pid) and is_alive(pid),
            console_log=project_root / "logs" / f"{config.stem}.console.log",
            last_log_at=max(times) if times else None, account=_account_of(config),
        ))
    return rows


def _symbol_of(config: Path) -> Optional[str]:
    try:
        return (yaml.safe_load(config.read_text(encoding="utf-8")) or {}).get("symbol_override")
    except Exception:  # noqa: BLE001  設定檔壞掉不能讓整頁掛掉
        return None


def _account_of(config: Path) -> str:
    try:
        return str((yaml.safe_load(config.read_text(encoding="utf-8")) or {}).get("account") or DEFAULT_ACCOUNT).strip()
    except Exception:  # noqa: BLE001
        return DEFAULT_ACCOUNT


def account_key(account: Optional[str]) -> str:
    """比對用:不分大小寫,舊紀錄沒帳戶 = test1。"""
    return (account or DEFAULT_ACCOUNT).strip().upper()


def stale_warning(alive: bool, last_log_at: Optional[datetime], now: datetime) -> Optional[str]:
    if not alive:
        return None
    if last_log_at is None:
        return "⚠️ 找不到 log"
    silent = now - last_log_at
    if silent > STALE_AFTER:
        return f"⚠️ {int(silent.total_seconds() // 60)} 分鐘沒寫 log(Mac 睡著或程式卡住?)"
    return None


def git_head(project_root: Path = PROJECT_ROOT) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(project_root), "rev-parse", "--short=7", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def code_version_label(run_commit: Optional[str], head: Optional[str]) -> str:
    """正在跑的程式是哪個 commit;跟 HEAD 不同 = 舊碼(新功能不一定生效)。"""
    if not run_commit:
        return "—"
    dirty = run_commit.endswith("-dirty")
    short = run_commit[:7] + ("-dirty" if dirty else "")
    if head and run_commit.startswith(head) and not dirty:
        return f"{short}(最新)"
    return f"{short} ⚠️ 舊碼(HEAD {head})"


# ---------------------------------------------------------------------------
# trading 資料庫(sl_run / sl_event)
# ---------------------------------------------------------------------------

def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _rows(db_url: str, stmt) -> List[Dict[str, Any]]:
    engine = create_engine(db_url, future=True)
    try:
        with Session(engine) as s:
            out = []
            for obj in s.scalars(stmt):
                row = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
                for k, v in row.items():
                    if isinstance(v, datetime):
                        row[k] = _utc(v)
                out.append(row)
            return out
    finally:
        engine.dispose()


def recent_runs(db_url: str, limit: int = 20) -> List[Dict[str, Any]]:
    return _rows(db_url, select(SlRun).order_by(SlRun.started_at.desc()).limit(limit))


def recent_events(db_url: str, limit: int = 20) -> List[Dict[str, Any]]:
    return _rows(db_url, select(SlEvent).order_by(SlEvent.end_time.desc()).limit(limit))


def unfinished_runs(db_url: str) -> Dict[tuple, Dict[str, Any]]:
    """(帳戶大寫, 幣) → 最新一段還沒結束的 run;同一個幣可以在不同帳戶各有一段(2026-10-12)。
    舊紀錄沒帳戶的,account 補成 test1 顯示。"""
    runs = _rows(db_url, select(SlRun).where(SlRun.ended_at.is_(None), SlRun.end_reason.is_(None))
                 .order_by(SlRun.started_at.asc()))
    out = {}
    for r in runs:
        r["account"] = r.get("account") or DEFAULT_ACCOUNT
        out[(account_key(r["account"]), r["symbol"])] = r
    return out


def free_style_running(db_url: str) -> List[Dict[str, Any]]:
    return [r for r in unfinished_runs(db_url).values() if r["strategy_name"] == FREE_STYLE]


HKT_ZONE = "Asia/Hong_Kong"


def fmt_time(dt: Optional[datetime]) -> str:
    """畫面上的時間只到分鐘(HKT),例 10-12 05:55。"""
    if dt is None:
        return "—"
    from zoneinfo import ZoneInfo

    return dt.astimezone(ZoneInfo(HKT_ZONE)).strftime("%m-%d %H:%M")


def fmt_duration(delta: Optional[timedelta]) -> str:
    """時間長度只到分鐘,例 4 天 1 小時 38 分。"""
    if delta is None:
        return "—"
    from strategy_lab.engine.hold_time import format_hold

    return format_hold(delta)


def schedule(config_path: Path, started_at: Optional[datetime]) -> Optional[Dict[str, Any]]:
    """啟動時間、預估結束(策略時間窗的強制收尾時間)、預估長度。啟動時間不知道或策略讀不到 → None。"""
    if started_at is None:
        return None
    try:
        from strategy_lab.dsl.loader import load_strategy

        cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        strategy_file = Path(cfg.get("strategy_path", ""))
        if not strategy_file.is_absolute():
            strategy_file = PROJECT_ROOT / strategy_file
        window = load_strategy(strategy_file).time_window
        from zoneinfo import ZoneInfo

        # 資料庫存的是 UTC;時間窗照 HKT 算(實盤 run_forever 傳的也是 HKT),先換成 HKT 再算,不然差 8 小時
        started_at = started_at.astimezone(ZoneInfo(HKT_ZONE))
        end = window.window_end(started_at) - timedelta(minutes=getattr(window, "cleanup_buffer_minutes", 0))
    except Exception:  # noqa: BLE001  設定 / 策略檔壞掉不能讓整頁掛掉
        return None
    # 畫面只到分鐘:長度用截到分鐘的啟動時間算,跟畫面上顯示的兩個時間對得上
    return {"start": started_at, "end": end, "duration": end - started_at.replace(second=0, microsecond=0)}


def mode_label(config_path: Path) -> str:
    """設定檔的實盤開關;沒寫的欄位用安全預設(dry_run / testnet 都是 true)。"""
    try:
        cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        cfg = {}
    if cfg.get("dry_run", True):
        return "⚪ DRY RUN"
    return "🟡 測試網" if cfg.get("testnet", True) else "🔴 實盤"


def active_runs(db_url: Optional[str], project_root: Path = PROJECT_ROOT) -> List[Dict[str, Any]]:
    """「策略」分頁的進行中清單:在跑的策略 daemon,加上進行中的 free style。"""
    unfinished = unfinished_runs(db_url) if db_url else {}
    rows = []
    for d in list_daemons(project_root):
        if not d.alive:
            continue
        run = unfinished.get((account_key(d.account), d.symbol))
        if run and run["strategy_name"] != FREE_STYLE:
            strategy, started = run["strategy_name"], run["started_at"]
        else:
            path = (yaml.safe_load((project_root / f"{d.config}.yaml").read_text(encoding="utf-8")) or {}).get("strategy_path")
            strategy, started = (Path(path).stem if path else "—"), None
        rows.append({"kind": "strategy", "config": d.config, "account": d.account, "symbol": d.symbol, "strategy": strategy,
                     "mode": mode_label(project_root / f"{d.config}.yaml"), "started_at": started})
    for r in unfinished.values():
        if r["strategy_name"] == FREE_STYLE:
            rows.append({"kind": "free style", "config": None, "account": r["account"], "symbol": r["symbol"], "strategy": FREE_STYLE,
                         "mode": "⏺ 記錄中", "started_at": r["started_at"]})
    return rows


# ---------------------------------------------------------------------------
# 損益表(2026-10-14):每一對交易(sl_event,持倉 0 → 0)一列
# ---------------------------------------------------------------------------

def pnl_strategies(db_url: str, account: str) -> List[str]:
    """這個帳戶有紀錄的策略;free style 永遠排第一(預設)。"""
    engine = create_engine(db_url, future=True)
    try:
        with Session(engine) as s:
            pairs = s.execute(select(SlEvent.account, SlEvent.strategy_name).distinct()).all()
    finally:
        engine.dispose()
    names = sorted({name for acc, name in pairs if name and account_key(acc) == account_key(account)} - {FREE_STYLE})
    return [FREE_STYLE, *names]


def pnl_rows(db_url: str, account: str, strategy: str) -> List[Dict[str, Any]]:
    """選帳戶 + 策略的每一對交易,最近的在前。run_id / event_index 給存檔用,畫面不顯示。
    真實損益 = 淨利(扣手續費、資金費);手續費佔比 = 手續費 ÷ 價差毛利(%);日期 = 平倉日(HKT)。"""
    engine = create_engine(db_url, future=True)
    try:
        with Session(engine) as s:
            events = list(s.scalars(select(SlEvent).where(SlEvent.strategy_name == strategy)))
            symbols = {r.run_id: r.symbol for r in s.scalars(select(SlRun).where(
                SlRun.run_id.in_({e.run_id for e in events})))} if events else {}
    finally:
        engine.dispose()
    events = [e for e in events if account_key(e.account) == account_key(account)]
    events.sort(key=lambda e: _utc(e.end_time), reverse=True)
    num = lambda v: float(v) if v is not None else None  # noqa: E731
    rows = []
    for e in events:
        gross, fees = num(e.realized_pnl), num(e.fees)
        rows.append({
            "run_id": e.run_id, "event_index": e.event_index,
            "日期": _hkt(e.end_time).strftime("%Y/%m/%d"), "幣種": symbols.get(e.run_id, ""), "方向": e.direction,
            "數量": num(e.max_position), "建倉價格": num(e.avg_entry), "建倉時間": _hkt(e.start_time).strftime("%m-%d %H:%M"),
            "平倉價格": num(e.avg_exit), "真實損益": num(e.net_pnl), "手續費": fees,
            "手續費佔比": round(fees / abs(gross) * 100, 2) if gross and fees is not None else None,
            "預估時長": hold_parts(e.expected_hold_seconds)[0], "預估單位": hold_parts(e.expected_hold_seconds)[1],
            "實際時長": fmt_duration(timedelta(seconds=e.hold_seconds)) if e.hold_seconds is not None else
            fmt_duration(_utc(e.end_time) - _utc(e.start_time)),
            "目標價": num(e.target_price), "停損價": num(e.stop_price), "備註": e.note or "",
            "實際秒數": e.hold_seconds if e.hold_seconds is not None else
            int((_utc(e.end_time) - _utc(e.start_time)).total_seconds()),
            "預估秒數": e.expected_hold_seconds,
        })
    return rows


SAT_STRATEGY = "sat_strategy"
END_REASON_LABELS = {"window_cleanup": "時間窗收尾", "stop_requested": "手動停止", "loop_done": "輪數做完",
                     "kill_switch": "風控停止", "crash": "當掉", "handover": "交接", "backfill": "補記"}


def weekend_of(dt: datetime) -> datetime:
    """這個時間屬於哪個週末:回傳那個週六的日期(HKT)。週末時間窗是週六 04:00 → 週一 06:00,
    週日、週一早上算前一個週六;週二到週五(手動補跑)也歸到前一個週六。"""
    local = _hkt(dt)
    return (local - timedelta(days=(local.weekday() - 5) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)


def sat_weekends(db_url: str, account: str) -> List[Dict[str, Any]]:
    """sat_strategy 分頁(2026-10-22):每個週末一列,最近的在前。起點價 = sl_run.origin_price;掛單價 = 那次執行的
    建倉限價單(非 reduceOnly 的限價單;舊程式沒成交的週末 Bybit 不保留,用 config.entry_price,來自舊程式 log)。
    同一個週末有好幾次執行(例 10-17 舊程式 → strategy_lab 接手)合成一列:損益 / 輪數 / 手續費相加,價格不同就用 → 連起來。
    run_id 給存備註用(存到那個週末最後一次執行),畫面不顯示。"""
    engine = create_engine(db_url, future=True)
    try:
        with Session(engine) as s:
            runs = [r for r in s.scalars(select(SlRun).where(SlRun.strategy_name == SAT_STRATEGY))
                    if account_key(r.account) == account_key(account)]
            ids = [r.run_id for r in runs]
            events = list(s.scalars(select(SlEvent).where(SlEvent.run_id.in_(ids)))) if ids else []
            orders = list(s.scalars(select(SlOrder).where(SlOrder.run_id.in_(ids)))) if ids else []
    finally:
        engine.dispose()
    num = lambda v: float(v) if v is not None else None  # noqa: E731

    def entry_prices(run: SlRun) -> List[str]:
        mine = sorted((o for o in orders if o.run_id == run.run_id and o.order_type == "limit" and not o.reduce_only
                       and o.purpose != "exit" and o.price is not None), key=lambda o: (o.side, float(o.price)))
        found = {}
        for o in mine:
            found.setdefault(o.side, f"{'買' if o.side == 'Buy' else '賣'} {float(o.price):.10g}")
        if found:
            return list(found.values())
        logged = (run.config or {}).get("entry_price")
        return [f"買 {float(logged):.10g}"] if logged is not None else []

    def joined(values: List[str]) -> str:
        out: List[str] = []
        for v in values:
            if v and v not in out:
                out.append(v)
        return " → ".join(out) if out else "—"

    groups: Dict[datetime, List[SlRun]] = {}
    for r in runs:
        groups.setdefault(weekend_of(r.started_at), []).append(r)
    rows = []
    for weekend, members in sorted(groups.items(), reverse=True):
        members.sort(key=lambda r: _utc(r.started_at))
        mine = [e for e in events if e.run_id in {r.run_id for r in members}]
        ended = [r for r in members if r.ended_at is not None]
        seconds = sum(int((_utc(r.ended_at) - _utc(r.started_at)).total_seconds()) for r in ended)
        rows.append({
            "run_id": members[-1].run_id,
            "週末": weekend.strftime("%Y/%m/%d") + "(六)",
            "開始": _hkt(members[0].started_at).strftime("%m-%d %H:%M"),
            "結束": _hkt(members[-1].ended_at).strftime("%m-%d %H:%M") if members[-1].ended_at else "進行中",
            "起點價": joined([f"{num(r.origin_price):.10g}" if r.origin_price is not None else "" for r in members]),
            "掛單價": joined([" / ".join(entry_prices(r)) for r in members]),
            "方向": joined([r.direction for r in members]),
            "輪數": len(mine),
            "數量": max((num(e.max_position) or 0 for e in mine), default=None) or None,
            "真實損益": sum(num(e.net_pnl) or 0 for e in mine),
            "手續費": sum(num(e.fees) or 0 for e in mine),
            "實際時長": fmt_duration(timedelta(seconds=seconds)) if ended else "—",
            "結束原因": joined([END_REASON_LABELS.get(r.end_reason or "", r.end_reason or "進行中") for r in members]),
            "執行次數": len(members),
            "來源": joined(["舊程式" if (r.strategy_path or "").startswith("~/sat_strategy") else "strategy_lab"
                          for r in members]),
            "備註": members[-1].note or "",
        })
    return rows


def fee_points(client: Any, symbol: str = "ETHUSDT") -> Dict[str, Any]:
    """手續費換成點數(使用者指定):現價 × maker 費率 × 2(買 + 賣)= 用 maker 開倉、maker 平倉,
    價格要往有利方向走幾點才賺回手續費。費率是這個帳戶在這個交易對的真實費率(只讀)。"""
    price = client.get_last_price(symbol)
    maker, _taker = client.get_fee_rates(symbol)
    return {"symbol": symbol, "price": price, "maker_rate": maker, "points": price * maker * 2}


def pnl_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """損益表最底的總結列(使用者指定):勝率(淨利 > 0 的對數 ÷ 總對數)、數量平均、真實損益總和、手續費總和、手續費佔比平均、
    實際時長平均、預估時長平均(只平均有值的);其他欄位空白。沒有資料 → {}。"""
    if not rows:
        return {}

    def avg(key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return sum(values) / len(values) if values else None

    actual, expected = avg("實際秒數"), avg("預估秒數")
    return {
        "日期": f"總結({len(rows)} 對)", "數量": avg("數量"),
        "勝率": sum(1 for r in rows if (r.get("真實損益") or 0) > 0) / len(rows),  # 淨利 > 0 才算贏
        "真實損益": sum(r["真實損益"] or 0 for r in rows), "手續費": sum(r["手續費"] or 0 for r in rows),
        "手續費佔比": avg("手續費佔比"),
        "實際時長": fmt_duration(timedelta(seconds=actual)) if actual is not None else "",
        "預估時長": fmt_duration(timedelta(seconds=expected)) if expected is not None else "",
    }


HOLD_UNITS = {"秒": 1, "分鐘": 60, "小時": 3600, "天": 86400}  # 預估時長的單位(下拉選)


def hold_parts(seconds: Optional[int]):
    """預估時長秒數 → (數字, 單位),表格裡數字自己打、單位用下拉選(2026-10-14 使用者)。沒填 = (None, "")。
    挑最大、又能寫成整數或 .5 的單位:5400 秒 → 90 分鐘,36 小時 → 1.5 天。"""
    if not seconds:
        return None, ""
    if seconds >= 86400 and seconds % 43200 == 0:
        value, unit = seconds / 86400, "天"
    elif seconds % 3600 == 0:
        value, unit = seconds / 3600, "小時"
    elif seconds % 60 == 0:
        value, unit = seconds / 60, "分鐘"
    else:
        value, unit = seconds, "秒"
    return (int(value) if float(value).is_integer() else value), unit


def hold_seconds(value: Any, unit: Optional[str]) -> Optional[int]:
    """(數字, 單位) → 秒數;數字空白 = 不填(None);單位空白當小時。"""
    if value is None or value == "" or (isinstance(value, float) and value != value):
        return None
    return round(float(value) * HOLD_UNITS[unit or "小時"])


def _hkt(dt: datetime) -> datetime:
    return _utc(dt).astimezone(ZoneInfo(HKT_ZONE))


# ---------------------------------------------------------------------------
# Bybit 帳戶(只讀)
# ---------------------------------------------------------------------------

def account_snapshot(client: Any) -> Dict[str, Any]:
    positions = [{
        "幣種": p["symbol"], "方向": "多" if p.get("side") == "Buy" else "空", "數量": float(p["size"]),
        "均價": float(p.get("avgPrice") or 0), "標記價": float(p.get("markPrice") or 0),
        "未實現": float(p.get("unrealisedPnl") or 0), "槓桿": p.get("leverage"),
    } for p in client.list_positions()]
    orders = [{
        "幣種": o["symbol"], "方向": o.get("side"), "類型": o.get("orderType"),
        "價格": float(o.get("price") or 0), "數量": float(o.get("qty") or 0), "只減倉": bool(o.get("reduceOnly")),
        "下單時間": datetime.fromtimestamp(int(o["createdTime"]) / 1000, tz=timezone.utc) if o.get("createdTime") else None,
        "orderId": str(o.get("orderId", ""))[:8],
    } for o in client.list_open_orders()]
    return {"equity": client.get_account_equity(), "positions": positions, "orders": orders}


def log_tail(path: Path, lines: int = 40) -> str:
    return _tail(Path(path), lines)


# ---------------------------------------------------------------------------
# 外部依賴(集中在這裡,測試可以換成假的)
# ---------------------------------------------------------------------------

def load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")


def db_url() -> Optional[str]:
    import os

    return os.getenv("TRADING_DB_URL")


UI_DEFAULT_ACCOUNT = "main"  # 頁面上帳戶選單預設選這個(2026-10-12);設定檔沒寫 account 仍是 test1(DEFAULT_ACCOUNT)


def list_accounts() -> List[str]:
    """.env 裡的帳戶名稱,頁面選單用:main 排第一(預設選它),接著 test1(可以用原本的 BYBIT_API_KEY,
    .env 沒寫也一定有),其他照 .env 順序。.env 沒有 main 就是 test1 排第一。"""
    from strategy_lab.live import accounts

    try:
        found = accounts.list_accounts()
    except OSError:  # 沒有 .env
        found = []
    test1 = next((a for a in found if a.upper() == DEFAULT_ACCOUNT.upper()), DEFAULT_ACCOUNT)
    names = [test1, *(a for a in found if a.upper() != DEFAULT_ACCOUNT.upper())]
    return sorted(names, key=lambda a: a.upper() != UI_DEFAULT_ACCOUNT.upper())  # 穩定排序:只把 main 移到最前面


def make_client(account: Optional[str] = None) -> Any:
    """只讀用的 Bybit client(mainnet),用這個帳戶的 key(None = test1);介面上不會呼叫任何下單 / 取消。"""
    from strategy_lab.live import accounts
    from strategy_lab.live.bybit_client import BybitClient

    creds = accounts.resolve(account)
    return BybitClient(api_key=creds.api_key, api_secret=creds.api_secret, testnet=False)


def project_root() -> Path:
    """live_*.yaml 所在的專案根目錄(測試會換成臨時資料夾)。"""
    return PROJECT_ROOT


def config_path(name: str) -> Path:
    """live_*.yaml 執行設定的路徑。"""
    return project_root() / f"{name}.yaml"
