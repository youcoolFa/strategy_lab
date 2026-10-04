"""分注買入法的 runner(建倉價由使用者輸入、數量照比重、每注各自平倉)。

跟 StrategyRunner 的差別只在「同時管理多注」:
- 一個 loop 開始時(IDLE),把每一注的建倉限價單一次掛上:
  第 k 注價格 = entry_prices[k],數量 = ScaleInEntry.lot_qtys(order_qty)[k]
  (order_qty 是第一注的數量,來自 position_sizing)。
- 某一注建倉成交 → 馬上掛「這一注」的 reduceOnly 平倉限價單:
  價格 = 這注的建倉價 ± ScaleOutExit 的距離,數量 = 這注的數量。
- 部位 0 → 0 = 1 個 loop(建 1 平 1、建 2 平 2、建 3 平 3 都只算 1 個)。
  loop 結束時取消還沒成交的建倉單,下一個 tick 全部重新掛上(選項 A)。
- 收攤條件(time_window / kill_switch / request_stop / loop 用完)跟
  StrategyRunner 一樣,收攤時取消每一注的掛單並市價平倉。

origin_price 這個策略不用(start() 仍照常記下啟動價,保留給日後用)。
沒有停損——規格目前刻意不做。

同一個 tick 內先處理建倉成交、再處理平倉成交:實盤是輪詢,兩次輪詢之間
可能同時有建倉跟平倉成交,先記建倉可以避免部位被誤判成短暫歸 0、提早
結束 loop。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from strategy_lab.engine.runner import RunState, StrategyRunner, Trade
from strategy_lab.interfaces import OrderLike


@dataclass
class Lot:
    index: int  # 第幾注,1 起算
    entry_price: float
    qty: float
    entry_order: Optional[OrderLike] = None
    exit_order: Optional[OrderLike] = None
    filled_price: Optional[float] = None  # 建倉成交價;None = 還沒建倉
    done: bool = False  # 這一注本 loop 已經平倉完成

    @property
    def holding(self) -> bool:
        # 建倉成交、還沒平倉;exit_order 可能是 None(平倉單網路失敗沒掛上,下一輪補掛)
        return self.filled_price is not None and not self.done


@dataclass
class ScaleInRunner(StrategyRunner):
    entry_prices: List[float] = field(default_factory=list)
    lots: List[Lot] = field(default_factory=list, init=False)
    _loop_event_base: int = field(default=0, init=False, repr=False)  # 這個 loop 開始時已完成的 event 數

    def __post_init__(self) -> None:
        super().__post_init__()
        n = self.entry.lots
        if len(self.entry_prices) != n:
            raise ValueError(f"entry_prices 要有 {n} 個價格(每注一個),目前是 {self.entry_prices}")
        if any(p <= 0 for p in self.entry_prices):
            raise ValueError(f"entry_prices 必須全部大於 0,目前是 {self.entry_prices}")
        self.entry_prices = [float(p) for p in self.entry_prices]

    @property
    def lot_qtys(self) -> List[float]:
        return self.entry.lot_qtys(self.order_qty)

    def tick(self, now: datetime, price: float) -> None:
        if not self._prelude(now, price):
            return
        if self.state == RunState.IDLE:
            self._place_entries(now)
            return
        self._sync_entries(now)
        self._sync_exits(now)
        # 跟 loop 開始時比,不跟這一輪開始時比:上一輪若在記完成交後網路失敗中斷,
        # event 已經多了一個,這一輪仍要能結束 loop。
        if len(self.events) > self._loop_event_base:
            self._end_loop(now)
        else:
            self._update_state()

    # --- 一個 loop 的流程 ---
    # 每一步失敗(網路)後,下一輪 tick 重做都是安全的:只補掛還沒掛上的單,
    # 不會重複掛已經掛上的(live/main.py 的 _tick_tolerating_network_errors)。

    def _place_entries(self, now: datetime) -> None:
        if not self.lots:
            self.lots = [
                Lot(index=i + 1, entry_price=p, qty=q)
                for i, (p, q) in enumerate(zip(self.entry_prices, self.lot_qtys))
            ]
            self._loop_event_base = len(self.events)
        for lot in self.lots:
            if lot.entry_order is None:
                self._place_entry(now, lot)
        self.state = RunState.ENTRY_PENDING

    def _place_entry(self, now: datetime, lot: Lot) -> None:
        if self.direction == "short":
            lot.entry_order = self.broker.limit_sell(price=lot.entry_price, qty=lot.qty)
        else:
            lot.entry_order = self.broker.place_limit_buy(price=lot.entry_price, qty=lot.qty)
        self._emit_order(now, "entry", lot.entry_order, self._side(entry=True), "limit", lot.entry_price,
                         lot.qty, reduce_only=False, status="open", lot=lot.index)

    def _place_exit(self, now: datetime, lot: Lot) -> None:
        price = self.exit.exit_price_for(lot.entry_price, self.direction)
        if self.direction == "short":
            lot.exit_order = self.broker.limit_flat_sell(price=price, qty=lot.qty)
        else:
            lot.exit_order = self.broker.place_limit_sell(price=price, qty=lot.qty)
        self._emit_order(now, "exit", lot.exit_order, self._side(entry=False), "limit", price,
                         lot.qty, reduce_only=True, status="open", lot=lot.index)

    def _sync_entries(self, now: datetime) -> None:
        for lot in self.lots:
            if lot.filled_price is not None:
                continue
            if lot.entry_order is None:
                self._place_entry(now, lot)  # 上一輪網路失敗沒掛上,補掛
                continue
            order = self.broker.fetch_order(lot.entry_order.id)
            if order.status == "closed":
                lot.filled_price = order.price
                lot.qty = order.filled_qty or lot.qty  # 平倉數量用實際成交量(交易所會修正精度)
                if self.entry_time is None:
                    self.entry_time = now
                self._update_order(now, order.id, "closed", order.price, order.filled_qty)
                self._record_fill(now, self._entry_sign(), order.filled_qty, order.price)
                self._place_exit(now, lot)
            elif order.status == "canceled":
                # 被外部取消(例如在 Bybit App 手動取消)→ 跟 StrategyRunner 一樣重新掛回去
                self._update_order(now, order.id, "canceled", None, order.filled_qty)
                self._place_entry(now, lot)
        self._refresh_active_entry_price()

    def _sync_exits(self, now: datetime) -> None:
        for lot in self.lots:
            if not lot.holding:
                continue
            if lot.exit_order is None:
                self._place_exit(now, lot)  # 建倉成交後平倉單網路失敗沒掛上,補掛
                continue
            order = self.broker.fetch_order(lot.exit_order.id)
            if order.status == "closed":
                self.trades.append(Trade(entry_price=lot.filled_price, exit_price=order.price,
                                         qty=order.filled_qty, direction=self.direction))
                self._update_order(now, order.id, "closed", order.price, order.filled_qty)
                lot.done = True  # 這一注本 loop 已完成;filled_price 保留 → 不會再掛建倉
                lot.exit_order = None  # 已成交,收尾時不用再取消
                self._record_fill(now, -self._entry_sign(), order.filled_qty, order.price)
            elif order.status == "canceled":
                self._update_order(now, order.id, "canceled", None, order.filled_qty)
                self._place_exit(now, lot)
        self._refresh_active_entry_price()

    def _end_loop(self, now: datetime) -> None:
        """部位回到 0:取消還沒成交的建倉單,下一個 tick 重新掛全部(選項 A)。"""
        self._cancel_open_orders(now)
        self.lots = []
        self.entry_time = None
        self.active_entry_price = None
        self.state = RunState.IDLE
        if self._loop_exhausted():
            self.stop_reason = "loop_done"
            self._cleanup(now)

    def _update_state(self) -> None:
        self.state = RunState.IN_POSITION if any(l.holding for l in self.lots) else RunState.ENTRY_PENDING

    def _refresh_active_entry_price(self) -> None:
        held = [l for l in self.lots if l.holding]
        qty = sum(l.qty for l in held)
        self.active_entry_price = sum(l.filled_price * l.qty for l in held) / qty if qty else None

    # --- 收攤 ---

    def _cancel_open_orders(self, now: datetime) -> None:
        for lot in self.lots:
            for order in (lot.entry_order, lot.exit_order):
                if order is not None:
                    self.broker.cancel_order(order.id)
                    self._update_order(now, order.id, "canceled", None, 0.0)
