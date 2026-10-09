"""Streamlit 介面的資料層(2026-10-09)。全部只讀:不下單、不取消、不動 daemon。
畫面(app.py)只負責顯示;這裡不 import streamlit,可以單獨測試。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from strategy_lab.live.daemon import _read_pid, _tail, is_alive
from strategy_lab.storage.models import SlEvent, SlRun

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 每小時一定有一則 ⚪ 狀態回報;運作中卻超過這麼久沒寫 log → Mac 睡著或程式卡住
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
            last_log_at=max(times) if times else None,
        ))
    return rows


def _symbol_of(config: Path) -> Optional[str]:
    try:
        return (yaml.safe_load(config.read_text(encoding="utf-8")) or {}).get("symbol_override")
    except Exception:  # noqa: BLE001  設定檔壞掉不能讓整頁掛掉
        return None


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


def unfinished_by_symbol(db_url: str) -> Dict[str, Dict[str, Any]]:
    """每個幣最新一段還沒結束的 run(ended_at 與 end_reason 都是 NULL)。"""
    runs = _rows(db_url, select(SlRun).where(SlRun.ended_at.is_(None), SlRun.end_reason.is_(None))
                 .order_by(SlRun.started_at.asc()))
    return {r["symbol"]: r for r in runs}  # 同一個幣有好幾段時,後面(較新)的蓋掉前面


def free_style_running(db_url: str) -> List[Dict[str, Any]]:
    return [r for r in unfinished_by_symbol(db_url).values() if r["strategy_name"] == FREE_STYLE]


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
    unfinished = unfinished_by_symbol(db_url) if db_url else {}
    rows = []
    for d in list_daemons(project_root):
        if not d.alive:
            continue
        run = unfinished.get(d.symbol)
        if run and run["strategy_name"] != FREE_STYLE:
            strategy, started = run["strategy_name"], run["started_at"]
        else:
            path = (yaml.safe_load((project_root / f"{d.config}.yaml").read_text(encoding="utf-8")) or {}).get("strategy_path")
            strategy, started = (Path(path).stem if path else "—"), None
        rows.append({"kind": "strategy", "config": d.config, "symbol": d.symbol, "strategy": strategy,
                     "mode": mode_label(project_root / f"{d.config}.yaml"), "started_at": started})
    for r in unfinished.values():
        if r["strategy_name"] == FREE_STYLE:
            rows.append({"kind": "free style", "config": None, "symbol": r["symbol"], "strategy": FREE_STYLE,
                         "mode": "⏺ 記錄中", "started_at": r["started_at"]})
    return rows


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


def make_client() -> Any:
    """只讀用的 Bybit client(mainnet);介面上不會呼叫任何下單 / 取消。"""
    from strategy_lab.live.bybit_client import BybitClient

    return BybitClient(testnet=False)


def project_root() -> Path:
    """live_*.yaml 所在的專案根目錄(測試會換成臨時資料夾)。"""
    return PROJECT_ROOT


def config_path(name: str) -> Path:
    """live_*.yaml 執行設定的路徑。"""
    return project_root() / f"{name}.yaml"
