"""
event = 部位從 0 開始、回到 0 結束的一段交易。由成交自動切出來,不用每個
策略自己定義:單筆進出(mean_reversion)是 1 買 1 平;分批加減碼(分注法)
是多次成交,部位回到 0 才算一個 event。

損益以帶正負號的部位做會計(多單正、空單負),加碼更新平均成本、減碼實現
損益,多空同一套公式。event 期間每個價格都量一次帳面(已實現 + 未實現),
最低點就是這個 event 的最大回撤——沒有停損不代表沒有回撤。

realized_pnl 是價差毛利,未扣手續費和資金費(那些要從交易所成交明細來)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

_EPS = 1e-12


@dataclass
class Event:
    index: int
    direction: str
    start_time: datetime
    end_time: datetime
    fills: int
    max_position: float
    avg_entry: float
    avg_exit: float
    realized_pnl: float
    max_drawdown: float  # <= 0
    forced: bool  # 由時間窗收尾/停止訊號強制平倉結束


@dataclass
class _Open:
    start_time: datetime
    direction: str
    fills: int = 0
    max_position: float = 0.0
    entry_qty: float = 0.0
    entry_value: float = 0.0
    exit_qty: float = 0.0
    exit_value: float = 0.0
    realized: float = 0.0
    worst: float = 0.0


@dataclass
class EventTracker:
    position: float = 0.0
    avg_cost: float = 0.0
    completed: int = 0
    _open: Optional[_Open] = field(default=None, repr=False)

    def on_fill(self, time: datetime, signed_qty: float, price: float, forced: bool = False) -> Optional[Event]:
        """signed_qty:買進正、賣出負。部位回到 0 時回傳完成的 Event。"""
        if self._open is None:
            self._open = _Open(start_time=time, direction="long" if signed_qty > 0 else "short")
        ev = self._open
        ev.fills += 1

        same_side = self.position == 0 or (self.position > 0) == (signed_qty > 0)
        if same_side:
            new_position = self.position + signed_qty
            self.avg_cost = (self.avg_cost * abs(self.position) + price * abs(signed_qty)) / abs(new_position)
            self.position = new_position
            ev.entry_qty += abs(signed_qty)
            ev.entry_value += price * abs(signed_qty)
        else:
            closed = min(abs(signed_qty), abs(self.position))
            direction = 1 if self.position > 0 else -1
            ev.realized += (price - self.avg_cost) * closed * direction
            ev.exit_qty += closed
            ev.exit_value += price * closed
            self.position += signed_qty
            if abs(self.position) < _EPS:
                self.position = 0.0

        ev.max_position = max(ev.max_position, abs(self.position))
        self._mark(price)

        if self.position != 0.0:
            return None
        self.completed += 1
        self.avg_cost = 0.0
        self._open = None
        return Event(
            index=self.completed,
            direction=ev.direction,
            start_time=ev.start_time,
            end_time=time,
            fills=ev.fills,
            max_position=ev.max_position,
            avg_entry=ev.entry_value / ev.entry_qty,
            avg_exit=ev.exit_value / ev.exit_qty if ev.exit_qty else 0.0,
            realized_pnl=ev.realized,
            max_drawdown=min(0.0, ev.worst),
            forced=forced,
        )

    def on_price(self, time: datetime, price: float) -> None:
        if self._open is not None:
            self._mark(price)

    def _mark(self, price: float) -> None:
        ev = self._open
        if ev is None:
            return
        equity = ev.realized + (price - self.avg_cost) * self.position
        ev.worst = min(ev.worst, equity)


@dataclass
class EventSummary:
    count: int
    forced: int
    wins: int
    total_pnl: float
    max_drawdown: float  # <= 0,跨 event 的權益曲線從高點到低點


def summarize(events: List[Event]) -> EventSummary:
    cumulative = peak = 0.0
    max_dd = 0.0
    for e in events:
        trough = cumulative + e.max_drawdown
        max_dd = min(max_dd, trough - peak)
        cumulative += e.realized_pnl
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)
    return EventSummary(
        count=len(events),
        forced=sum(1 for e in events if e.forced),
        wins=sum(1 for e in events if e.realized_pnl > 0),
        total_pnl=cumulative,
        max_drawdown=max_dd,
    )
