"""
strategy_lab/live/adopt.py

接手現有持倉(2026-10-07):重新啟動時**不平倉**,把交易所上這個 symbol 的持倉與掛單對應回分注策略的
每一注,接著照常運作。設定檔要明確打開 `adopt_existing_position: true` 才會做(沒打開時,有殘留掛單/持倉
仍然拒絕啟動,見 live/main.py 的 ensure_clean_start)。

搭配 `python -m strategy_lab.live.daemon detach`:舊程式「脫離」(直接結束、不收尾),掛單與持倉原封不動
留在交易所,新程式啟動時接手。用途:換新版程式、改不能熱改的參數,又不想被迫市價平倉認賠。

對應規則(都用策略設定推算,對不上就 AdoptionError,絕不亂猜):
    - 只減倉(reduceOnly)、方向是平倉方的單 → 某一注的平倉單:數量 = 那注數量,價格 ≈ 那注建倉價 ± 平倉距離
      (容許 0.02% 誤差,交易所會把價格修整到 tick)→ 那一注視為已持有
    - 非只減倉、方向是建倉方的單 → 某一注的建倉單:數量 = 那注數量,價格 ≈ 那注建倉價
    - 已持有的注必須是 1..k 連續(依序掛單),建倉單最多一張、而且是第 k+1 注
    - 已持有各注數量加總 = 交易所持倉,方向一致
    - 認不出的單、持倉對不上、有持倉卻沒有平倉單 → 拒絕,請人工處理

限制:各注的建倉成交價拿不到個別數字,一律用交易所持倉均價(合計損益正確);舊程式那幾張建倉單的成交
手續費不在這次執行的紀錄裡,Telegram 的淨利會少扣這部分;loop 從這次啟動重新算。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from strategy_lab.engine.runner import RunState
from strategy_lab.live.broker import LiveOrder

PRICE_TOLERANCE = 2e-4  # 0.02%:交易所把價格修整到 tick 的誤差;注與注之間通常差 0.05% 以上


class AdoptionError(ValueError):
    pass


@dataclass
class MatchedOrder:
    lot: int
    order_id: str
    side: str
    price: float
    qty: float


@dataclass
class AdoptionPlan:
    position: float  # 交易所持倉(多正空負)
    avg_price: float  # 交易所持倉均價
    held: List[MatchedOrder] = field(default_factory=list)  # 已持有的注 → 它的平倉單
    pending_entry: Optional[MatchedOrder] = None  # 正掛著的下一注建倉單

    def describe(self) -> str:
        lines = [f"持倉 {self.position:g} @ 均價 {self.avg_price:g}" if self.position else "持倉 0"]
        for h in self.held:
            lines.append(f"第{h.lot}注 已持有 {h.qty:g},平倉單 {h.side} @ {h.price:g}")
        if self.pending_entry:
            e = self.pending_entry
            lines.append(f"第{e.lot}注 建倉單 {e.side} {e.qty:g} @ {e.price:g}(等成交)")
        return "\n".join(lines)


def _close(a: float, b: float, rel: float) -> bool:
    return abs(a - b) <= max(abs(b) * rel, 1e-12)


def plan_adoption(entry_prices: Sequence[float], lot_qtys: Sequence[float], exit_plugin: Any, direction: str,
                  open_orders: List[Dict[str, Any]], position: float, avg_price: float) -> AdoptionPlan:
    entry_side, exit_side = ("Sell", "Buy") if direction == "short" else ("Buy", "Sell")
    exit_prices = [exit_plugin.exit_price_for(p, direction) for p in entry_prices]
    held: Dict[int, MatchedOrder] = {}
    entries: Dict[int, MatchedOrder] = {}

    for o in open_orders:
        side, price, qty = o.get("side"), float(o.get("price") or 0), float(o.get("qty") or 0)
        reduce_only = str(o.get("reduceOnly")).lower() in ("true", "1")
        is_exit = reduce_only and side == exit_side
        is_entry = not reduce_only and side == entry_side
        targets = exit_prices if is_exit else entry_prices if is_entry else []
        taken = held if is_exit else entries
        match = None
        for i, (target, lot_qty) in enumerate(zip(targets, lot_qtys), 1):
            if entry_prices[i - 1] <= 0:
                continue  # 這一注沒在用(建倉價 0)
            if i not in taken and _close(qty, lot_qty, 1e-6) and _close(price, target, PRICE_TOLERANCE):
                match = i
                break
        if match is None:
            kind = "平倉單" if is_exit else "建倉單" if is_entry else "單"
            raise AdoptionError(
                f"認不出交易所上的{kind} {o.get('orderId')}({side} {qty:g} @ {price:g},reduceOnly={reduce_only}):"
                "對不上任何一注的設定(建倉價、平倉距離、數量),請先人工處理")
        taken[match] = MatchedOrder(match, str(o.get("orderId")), side, price, qty)

    held_lots = sorted(held)
    if held_lots != list(range(1, len(held_lots) + 1)):
        raise AdoptionError(f"已持有的注是 {held_lots},不是從第 1 注開始依序成交,和依序掛單的規則不符,請人工處理")
    if len(entries) > 1:
        raise AdoptionError(f"交易所上有 {len(entries)} 張建倉單,依序掛單最多只會有一張,請人工處理")
    pending = next(iter(entries.values()), None)
    if pending is not None and pending.lot != len(held_lots) + 1:
        raise AdoptionError(f"正掛著的是第{pending.lot}注建倉單,但已持有到第{len(held_lots)}注,順序對不上,請人工處理")

    sign = -1.0 if direction == "short" else 1.0
    held_qty = sum(h.qty for h in held.values())
    if position and not held:
        raise AdoptionError(f"交易所有持倉 {position:g},但找不到對應的平倉單,無法判斷是哪幾注,請人工處理")
    if not _close(position, sign * held_qty, 1e-6) and not (position == 0 and held_qty == 0):
        raise AdoptionError(f"交易所持倉 {position:g},但對應到的注合計 {sign * held_qty:g},持倉對不上,請人工處理")
    if held and avg_price <= 0:
        raise AdoptionError("拿不到交易所的持倉均價,無法接手")
    return AdoptionPlan(position=position, avg_price=avg_price, held=[held[i] for i in held_lots], pending_entry=pending)


def apply_adoption(runner: Any, plan: AdoptionPlan, now: datetime) -> List[str]:
    """把對應結果裝進 ScaleInRunner(要在 runner.start() 之後、第一個 tick 之前)。不下單、不取消任何單。"""
    from strategy_lab.engine.scale_in_runner import Lot

    runner.lots = [Lot(index=i, entry_price=p, qty=q) for i, p, q in runner.active_lots]
    runner._loop_event_base = len(runner.events)
    for h in plan.held:
        lot = runner.lots[h.lot - 1]
        lot.qty = h.qty
        lot.filled_price = plan.avg_price  # 個別成交價拿不到,用交易所均價(合計損益正確)
        lot.exit_order = LiveOrder(id=h.order_id, status="open", price=h.price, side=h.side, qty=h.qty, reduce_only=True)
        runner._emit_order(now, "exit", lot.exit_order, h.side, "limit", h.price, h.qty, reduce_only=True,
                           status="open", lot=h.lot)
    if plan.pending_entry is not None:
        e = plan.pending_entry
        lot = runner.lots[e.lot - 1]
        lot.entry_order = LiveOrder(id=e.order_id, status="open", price=e.price, side=e.side, qty=e.qty)
        runner._emit_order(now, "entry", lot.entry_order, e.side, "limit", e.price, e.qty, reduce_only=False,
                           status="open", lot=e.lot)
    if plan.position:
        runner._tracker.on_fill(now, plan.position, plan.avg_price)  # 讓 loop 結算從交易所均價算起
        runner.entry_time = now
    runner._refresh_active_entry_price()
    runner.state = RunState.IN_POSITION if plan.held else RunState.ENTRY_PENDING
    return plan.describe().split("\n")
