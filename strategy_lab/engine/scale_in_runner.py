"""分注買入法的 runner(建倉價由使用者輸入、數量照比重、每注各自平倉)。

跟 StrategyRunner 的差別只在「同時管理多注」:
- **依序掛單**(2026-10-06 使用者決定):一個 loop 開始時(IDLE)只掛第一注的建倉限價單;
  第 k 注建倉成交,才掛第 k+1 注。第 k 注價格 = entry_prices[k],
  數量 = ScaleInEntry.lot_qtys(order_qty)[k](order_qty 是第一注的數量,來自 position_sizing)。
  原本是三注一起掛;改成依序掛,交易所上一次只有一張建倉單、保證金只佔一張。
  代價:價格一口氣跌穿好幾個價位時,下一注要等偵測到前一注成交(下一個輪詢,約 5 秒)
  才掛,掛上時價格可能已經在價位之下 → 限價買單會立刻以吃單成交(價格不差,手續費較高)。
- 某一注建倉成交 → 同一個 tick 馬上掛「這一注」的 reduceOnly 平倉限價單(價格 = 這注的
  建倉價 ± ScaleOutExit 的距離,數量 = 這注的數量),以及下一注的建倉單。
- 部位 0 → 0 = 1 個 loop(建 1 平 1、建 2 平 2、建 3 平 3 都只算 1 個)。
  loop 結束時取消還沒成交的建倉單,下一個 tick 從第一注重新開始(選項 A)。
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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from strategy_lab.engine.hold_time import LotHold, format_hold
from strategy_lab.engine.runner import RunState, StrategyRunner, Trade
from strategy_lab.plugins.exit.scale_out import ScaleOutExit
from strategy_lab.interfaces import OrderLike


def validate_entry_prices(prices: Any, lots: int) -> List[float]:
    """分注的建倉價:每注一個;第二、三注…填 0 = 沒有這一注(2026-10-07)。
    規則:第一注一定要有(> 0);不能是負數;有第二注才有第三注——一旦出現 0,後面都要是 0。
    例:[1000, 990, 980] 三注、[1000, 990, 0] 兩注、[1000, 0, 0] 一注;[1000, 0, 980] 不行。"""
    try:
        prices = [float(p) for p in prices]
    except (TypeError, ValueError):
        raise ValueError(f"entry_prices 要是數字清單,收到 {prices!r}") from None
    if len(prices) != lots:
        raise ValueError(f"entry_prices 要有 {lots} 個價格(每注一個,不用的注填 0),收到 {prices}")
    if any(p < 0 for p in prices):
        raise ValueError(f"entry_prices 不能是負數,收到 {prices}")
    if prices[0] <= 0:
        raise ValueError(f"第一注一定要有建倉價(> 0),收到 {prices}")
    for i in range(1, lots - 1):
        if prices[i] == 0 and any(p > 0 for p in prices[i + 1:]):
            raise ValueError(f"有第二注才有第三注:第{i + 1}注是 0(不用),後面的注也要是 0,收到 {prices}")
    return prices


@dataclass
class Lot:
    index: int  # 第幾注,1 起算
    entry_price: float
    qty: float
    entry_order: Optional[OrderLike] = None
    exit_order: Optional[OrderLike] = None
    filled_price: Optional[float] = None  # 建倉成交價;None = 還沒建倉
    done: bool = False  # 這一注本 loop 已經平倉完成
    filled_at: Optional[datetime] = None  # 建倉成交時間(持倉計時從這裡算;接手的注 = 接手時間)
    hold_warned: bool = False  # 已經發過「超過預估持倉時間」警告

    @property
    def holding(self) -> bool:
        # 建倉成交、還沒平倉;exit_order 可能是 None(平倉單網路失敗沒掛上,下一輪補掛)
        return self.filled_price is not None and not self.done


@dataclass
class ScaleInRunner(StrategyRunner):
    entry_prices: List[float] = field(default_factory=list)
    # 預估持倉時間(策略 YAML 的 expected_hold):某一注持倉超過就 WARNING 一次(🟡),不自動平倉
    expected_hold: Optional[timedelta] = None
    lots: List[Lot] = field(default_factory=list, init=False)
    lot_holds: List[LotHold] = field(default_factory=list, init=False)  # 每注平倉後的持倉時間(結束總結用)
    _loop_event_base: int = field(default=0, init=False, repr=False)  # 這個 loop 開始時已完成的 event 數

    def __post_init__(self) -> None:
        super().__post_init__()
        self.entry_prices = validate_entry_prices(self.entry_prices, self.entry.lots)

    @property
    def lot_qtys(self) -> List[float]:
        return self.entry.lot_qtys(self.order_qty)

    @property
    def active_lots(self) -> List[Tuple[int, float, float]]:
        """有在用的注:(第幾注, 建倉價, 數量);建倉價 0 的注不算。"""
        return [(i, p, q) for i, (p, q) in enumerate(zip(self.entry_prices, self.lot_qtys), 1) if p > 0]

    def tick(self, now: datetime, price: float) -> None:
        if not self._prelude(now, price):
            return
        if self.state == RunState.IDLE:
            self._place_entries(now)
            return
        self._sync_entries(now)
        self._sync_exits(now)
        self._warn_long_holds(now)
        # 跟 loop 開始時比,不跟這一輪開始時比:上一輪若在記完成交後網路失敗中斷,
        # event 已經多了一個,這一輪仍要能結束 loop。
        if len(self.events) > self._loop_event_base:
            self._end_loop(now)
        else:
            self._update_state()

    # --- 一個 loop 的流程 ---
    # 每一步失敗(網路)後,下一輪 tick 重做都是安全的:只補掛還沒掛上的單,
    # 不會重複掛已經掛上的(live/main.py 的 _tick_tolerating_network_errors)。

    def _ready(self, lot: Lot) -> bool:
        """依序掛單:第一注隨時可以掛;第 k 注要等第 k−1 注建倉成交。"""
        return lot.index == 1 or self.lots[lot.index - 2].filled_price is not None

    def _place_entries(self, now: datetime) -> None:
        if not self.lots:
            self.lots = [Lot(index=i, entry_price=p, qty=q) for i, p, q in self.active_lots]
            self._loop_event_base = len(self.events)
        for lot in self.lots:
            if lot.entry_order is None and self._ready(lot):
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
        # 依注數順序處理:第 k 注在這一輪成交,迴圈走到第 k+1 注時它已經 ready,同一個 tick 就掛上
        for lot in self.lots:
            if lot.filled_price is not None:
                continue
            if lot.entry_order is None:
                if self._ready(lot):
                    self._place_entry(now, lot)  # 前一注剛成交,或上一輪網路失敗沒掛上 → 掛
                continue
            order = self.broker.fetch_order(lot.entry_order.id)
            if order.status == "closed":
                lot.filled_price = order.price
                lot.filled_at = now
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
                held = self._hold(lot, now, forced=False)
                self._update_order(now, order.id, "closed", order.price, order.filled_qty,
                                   hold_seconds=held.seconds if held else None)
                lot.done = True  # 這一注本 loop 已完成;filled_price 保留 → 不會再掛建倉
                lot.exit_order = None  # 已成交,收尾時不用再取消
                self._record_fill(now, -self._entry_sign(), order.filled_qty, order.price)
            elif order.status == "canceled":
                self._update_order(now, order.id, "canceled", None, order.filled_qty)
                self._place_exit(now, lot)
        self._refresh_active_entry_price()

    # --- 持倉計時(2026-10-09)---

    def _hold(self, lot: Lot, now: datetime, forced: bool) -> Optional[LotHold]:
        if lot.filled_at is None:
            return None
        held = LotHold(index=lot.index, event_index=self._tracker.completed + 1,
                       seconds=(now - lot.filled_at).total_seconds(), forced=forced)
        self.lot_holds.append(held)
        return held

    def _warn_long_holds(self, now: datetime) -> None:
        if self.expected_hold is None:
            return
        for lot in self.lots:
            if lot.holding and lot.filled_at is not None and not lot.hold_warned \
                    and now - lot.filled_at > self.expected_hold:
                lot.hold_warned = True
                logger.warning(f"⏰ 第{lot.index}注已持倉 {format_hold(now - lot.filled_at)},超過預估 "
                               f"{format_hold(self.expected_hold)}(建倉 {lot.filled_price:g};只是提醒,不會自動平倉)")

    def _cleanup(self, now: datetime) -> None:
        for lot in self.lots:  # 收尾時還拿著的注:持倉到市價平倉這一刻
            if lot.holding:
                self._hold(lot, now, forced=True)
        super()._cleanup(now)

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

    # --- 運作中改參數(建倉價、平倉距離、loop)---

    def apply_changes(self, now: datetime, price: float, changes: Dict[str, Any]) -> List[str]:
        """先全部驗證,都通過才改(不會改到一半失敗)。回傳給人看的變更說明。
        - 建倉價:還沒成交的注取消重掛 / 還沒輪到的之後用新價;已成交的注不動
        - 平倉距離:已持有的注取消重掛平倉單;之後成交的注用新距離
        - 取消前一刻剛好成交 → 不重掛(交給下一個 tick 的正常成交流程),避免重複下單"""
        unsupported = sorted(set(changes) - {"entry_prices", "distance", "loop"})
        if unsupported:
            raise ValueError(f"不支援在運作中改 {unsupported};可以改 entry_prices、distance、loop")
        new_prices = self._validate_entry_prices(changes["entry_prices"], price) if "entry_prices" in changes else None
        new_exit = ScaleOutExit(distance=dict(changes["distance"])) if "distance" in changes else None
        if "loop" in changes:
            self._validate_loop(changes["loop"])

        messages: List[str] = []
        if "loop" in changes:
            messages.append(self._set_loop(changes["loop"]))
        if new_exit is not None:
            messages += self._set_exit(now, new_exit)
        if new_prices is not None:
            messages += self._set_entry_prices(now, new_prices)
        return messages

    def _validate_entry_prices(self, prices: Any, price: float) -> List[float]:
        prices = validate_entry_prices(prices, self.entry.lots)
        for lot in self.lots:
            if lot.filled_price is not None and prices[lot.index - 1] == 0:
                raise ValueError(f"第{lot.index}注已成交,不能改成 0(不用);要等這個 loop 結束")
        # 會「立刻」掛在交易所上的注:正在掛著的,或還沒開始這個 loop 時的第一注
        now_placed = [l.index for l in self.lots if l.entry_order is not None and l.filled_price is None]
        if not self.lots:
            now_placed = [1]
        for index in now_placed:
            p = prices[index - 1]
            if p == 0:
                continue  # 改成不用 → 會取消,不會掛
            crosses = p <= price if self.direction == "short" else p >= price
            if crosses:
                side = "低於" if self.direction == "short" else "高於"
                raise ValueError(f"第{index}注新建倉價 {p:g} 越過現價 {price:g}({side}現價會立刻吃單成交)")
        return prices

    def _set_entry_prices(self, now: datetime, prices: List[float]) -> List[str]:
        old_prices, self.entry_prices = self.entry_prices, prices
        messages = []
        for lot in list(self.lots):
            new = prices[lot.index - 1]
            if lot.filled_price is not None:
                messages.append(f"第{lot.index}注已成交,建倉價維持 {lot.entry_price:g}")
                continue
            if new == 0:  # 改成不用這一注
                if lot.entry_order is not None and not self._cancel_for_replace(now, lot.entry_order):
                    messages.append(f"第{lot.index}注取消前已成交,保留這一注(下一輪照常處理成交)")
                    continue
                self.lots.remove(lot)
                messages.append(f"第{lot.index}注改成 0:不再建倉" + ("(已取消掛單)" if lot.entry_order else ""))
                continue
            if lot.entry_order is not None:
                if not self._cancel_for_replace(now, lot.entry_order):
                    messages.append(f"第{lot.index}注改價前已成交,維持 {lot.entry_price:g}(下一輪照常處理成交)")
                    continue
                old, lot.entry_price = lot.entry_price, new
                self._place_entry(now, lot)
                messages.append(f"第{lot.index}注建倉價 {old:g} → {new:g}(已重掛)")
            else:
                old, lot.entry_price = lot.entry_price, new
                messages.append(f"第{lot.index}注建倉價 {old:g} → {new:g}(還沒掛,輪到時用新價)")
        if self.lots:  # loop 進行中:從 0 加回來的注接在後面,輪到時掛
            existing = {lot.index for lot in self.lots}
            for i, p, q in self.active_lots:
                if i not in existing:
                    self.lots.append(Lot(index=i, entry_price=p, qty=q))
                    messages.append(f"新增第{i}注 @ {p:g} × {q:g}(前一注成交後掛)")
        else:
            messages.append("建倉價 " + "、".join(f"{o:g} → {n:g}" for o, n in zip(old_prices, prices)) + "(下一個 loop 開始時掛)")
        return messages

    def _set_exit(self, now: datetime, new_exit: ScaleOutExit) -> List[str]:
        old = self.exit
        self.exit = new_exit
        messages = [f"平倉距離 {old.value:g}{old.unit} → {new_exit.value:g}{new_exit.unit}"]
        for lot in self.lots:
            if not lot.holding or lot.exit_order is None:
                continue
            old_price = old.exit_price_for(lot.entry_price, self.direction)
            if not self._cancel_for_replace(now, lot.exit_order):
                messages.append(f"第{lot.index}注改價前已平倉成交(下一輪照常處理)")
                continue
            self._place_exit(now, lot)
            new_price = new_exit.exit_price_for(lot.entry_price, self.direction)
            messages.append(f"第{lot.index}注平倉單 {old_price:g} → {new_price:g}(已重掛)")
        return messages

    def _cancel_for_replace(self, now: datetime, order) -> bool:
        """取消一張單準備重掛。回傳 True = 已取消可以重掛;False = 取消前已成交,不能重掛。"""
        self.broker.cancel_order(order.id)
        fetched = self.broker.fetch_order(order.id)
        if fetched.status == "closed":
            return False
        self._update_order(now, order.id, "canceled", None, fetched.filled_qty)
        return True

    # --- 收攤 ---

    def _cancel_open_orders(self, now: datetime) -> None:
        for lot in self.lots:
            for order in (lot.entry_order, lot.exit_order):
                if order is not None:
                    self.broker.cancel_order(order.id)
                    self._update_order(now, order.id, "canceled", None, 0.0)
