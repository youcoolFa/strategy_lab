"""
strategy_lab/live/status.py

Telegram 上「看得懂現在在幹嘛」的訊息(2026-10-06):
    - start_message():啟動時的策略參數、各注/origin、最大部位、loop 次數、收尾時間
    - status_message():每小時的狀態回報(心跳)——狀態、部位與未實現盈虧、掛單與距現價、
      第幾個 loop、累計損益、離收尾多久。收到 = 還活著;超過時間沒收到 = 出事了。
    - StatusReporter:run_forever() 每個 tick 呼叫 maybe_report(),時間到才發
      (⚪ #狀態)。間隔:.env 的 STATUS_INTERVAL_MINUTES,預設 60,0 = 關閉。

用語統一叫 loop:runner 裡的 event(部位 0 → 0 一整輪)就是一個 loop。

全部只讀 runner 自己的狀態(EventTracker 的部位/均價、還開著的單),不另外打交易所 API。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml
from loguru import logger

from strategy_lab.engine.runner import RunState

DEFAULT_STATUS_INTERVAL_MINUTES = 60
_PURPOSE_LABELS = {"entry": "進場", "exit": "平倉", "forced_close": "強制平倉"}
_STOP_REASONS = {
    "window_cleanup": "窗口到期收尾",
    "kill_switch": "觸發 kill switch",
    "stop_requested": "收到停止訊號",
    "loop_done": "loop 全部完成",
}


def status_interval_minutes() -> float:
    raw = os.getenv("STATUS_INTERVAL_MINUTES")
    if raw is None or raw == "":
        return DEFAULT_STATUS_INTERVAL_MINUTES
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_STATUS_INTERVAL_MINUTES
    return max(value, 0.0)


def mode_label(config: Any) -> str:
    if config.dry_run:
        return "DRY RUN,不會真的下單"
    return "測試網" if config.testnet else "實盤"


def loop_progress(current: int, loop_setting: Optional[int]) -> str:
    total = "不限次數" if loop_setting is None else f"共 {loop_setting + 1} 個"
    return f"第 {current} 個 loop({total})"


def stop_reason_label(reason: Optional[str]) -> str:
    if reason is None:
        return "未知"
    return f"{_STOP_REASONS.get(reason, reason)}({reason})"


def _duration(delta: timedelta) -> str:
    minutes = max(int(delta.total_seconds() // 60), 0)
    return f"{minutes // 60} 小時 {minutes % 60} 分"


def _cleanup_at(runner: Any) -> Optional[datetime]:
    if runner.window_end is None:
        return None
    return runner.window_end - timedelta(minutes=getattr(runner.time_window, "cleanup_buffer_minutes", 0))


def _strategy_params(config: Any) -> str:
    try:
        raw = yaml.safe_load(Path(config.strategy_path).read_text(encoding="utf-8")) or {}
    except OSError:
        return ""
    parts = []
    for key, label in (("entry", "進場"), ("exit", "平倉")):
        spec = raw.get(key) or {}
        parts.append(f"{label} {spec.get('type')} {spec.get('params') or {}}")
    return "|".join(parts)


def _is_scale_in(runner: Any) -> bool:
    return hasattr(runner, "entry_prices") and hasattr(runner, "lot_qtys")


def _total_pnl(runner: Any) -> float:
    return sum(e.realized_pnl for e in runner.events)


def start_message(runner: Any, config: Any, symbol: str, price: float, now: datetime,
                  interval_minutes: float) -> str:
    name = Path(config.strategy_path).stem
    lines = [f"🚀 strategy_lab 啟動({mode_label(config)})", f"策略 {name}({runner.direction})|{symbol}"]
    params = _strategy_params(config)
    if params:
        lines.append(params)
    if _is_scale_in(runner):
        for i, (p, q) in enumerate(zip(runner.entry_prices, runner.lot_qtys), 1):
            lines.append(f"第{i}注 {p} × {q:g} → 平倉 {runner.exit.exit_price_for(p, runner.direction)}")
        lines.append("依序掛單:先掛第1注,前一注成交才掛下一注")
        max_qty = round(sum(runner.lot_qtys), 8)
        notional = sum(p * q for p, q in zip(runner.entry_prices, runner.lot_qtys))
    else:
        lines.append(f"origin {runner.origin_price}|數量 {runner.order_qty:g}")
        max_qty, notional = runner.order_qty, runner.order_qty * price
    lines.append(f"最大部位 {max_qty:g}(名義約 {notional:.2f} USDT)")
    total = "不限次數" if runner.loop is None else f"共 {runner.loop + 1} 個"
    lines.append(f"現價 {price}|loop {total}")
    cleanup = _cleanup_at(runner)
    if cleanup is not None:
        lines.append(f"收尾 {cleanup:%Y-%m-%d %H:%M} HKT(還有 {_duration(cleanup - now)})")
    if interval_minutes > 0:
        lines.append(f"每 {interval_minutes:g} 分鐘回報一次狀態")
    return "\n".join(lines)


def _state_label(runner: Any, position: float) -> str:
    if runner.state == RunState.STOPPED:
        return "已停止"
    if position != 0:
        return "持倉中" if runner.state != RunState.EXIT_PENDING else "持倉中,等待平倉成交"
    if runner.state == RunState.IDLE:
        return "準備掛單"
    return "等待進場"


def status_message(runner: Any, config: Any, symbol: str, price: float, now: datetime, started_at: datetime) -> str:
    tracker = runner._tracker  # 只讀:EventTracker 記著目前部位、均價、完成幾個 loop
    position, avg = tracker.position, tracker.avg_cost
    name = Path(config.strategy_path).stem
    lines = [
        f"⏱ strategy_lab 運作中({mode_label(config)})|已運行 {_duration(now - started_at)}",
        f"{name}({runner.direction})|{symbol}|現價 {price}",
        f"狀態:{_state_label(runner, position)}({loop_progress(tracker.completed + 1, runner.loop)})",
    ]
    if position != 0:
        side = "多" if position > 0 else "空"
        lines.append(f"部位 {side} {abs(position):g} @ {avg}|未實現 {(price - avg) * position:+.4f}")
    else:
        lines.append("部位 0")
    orders = sorted(runner._open_records.values(), key=lambda r: (r.purpose != "exit", r.lot or 0))
    if orders:
        lines.append("掛單:")
        for r in orders:
            lot = f"第{r.lot}注," if r.lot else ""
            distance = f"距現價 {(r.price / price - 1) * 100:+.2f}%" if r.price else "市價"
            lines.append(f"  {_PURPOSE_LABELS.get(r.purpose, r.purpose)} {r.side} {r.qty:g} @ {r.price}({lot}{distance})")
    else:
        lines.append("掛單:無")
    waiting = [l for l in getattr(runner, "lots", []) if l.entry_order is None and l.filled_price is None]
    if waiting:
        lines.append("待掛(前一注成交後才掛):" + "、".join(f"第{l.index}注 {l.entry_price} × {l.qty:g}" for l in waiting))
    lines.append(f"已完成 {len(runner.events)} 個 loop|累計損益 {_total_pnl(runner):+.4f} USDT(未扣手續費)")
    cleanup = _cleanup_at(runner)
    if cleanup is not None:
        lines.append(f"收尾還有 {_duration(cleanup - now)}({cleanup:%m-%d %H:%M})")
    return "\n".join(lines)


class StatusReporter:
    """每 interval_minutes 發一次 status_message()。第一次在啟動後一個間隔(啟動當下已經有啟動訊息)。"""

    def __init__(self, runner: Any, config: Any, symbol: str, started_at: datetime,
                 interval_minutes: Optional[float] = None) -> None:
        self.runner, self.config, self.symbol = runner, config, symbol
        self.started_at = started_at
        self.interval = status_interval_minutes() if interval_minutes is None else interval_minutes
        self._next = started_at + timedelta(minutes=self.interval)

    def maybe_report(self, now: datetime, price: float) -> bool:
        if self.interval <= 0 or now < self._next or self.runner.state == RunState.STOPPED:
            return False  # 已停止:接著會有結束訊息,不另外發狀態
        while self._next <= now:  # 電腦睡著醒來後不要一次補發好幾則
            self._next += timedelta(minutes=self.interval)
        try:
            text = status_message(self.runner, self.config, self.symbol, price, now, self.started_at)
        except Exception as e:  # noqa: BLE001  狀態訊息出錯不能影響交易
            logger.bind(telegram=False).warning(f"狀態回報組裝失敗:{e}")
            return False
        logger.bind(telegram=True, category="status").info(text)
        return True
