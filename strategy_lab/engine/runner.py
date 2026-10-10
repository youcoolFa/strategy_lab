"""
主迴圈(orchestrator loop),泛化自 sat_strategy/app/bot.py 的
SatStrategyBot.run():idle → 下單進場 → 成交 → 下單出場 → 成交 →
回到 idle,由 TimeWindow 把關開始/結束。

`broker` 欄位型別是 `interfaces.Broker` 這個 Protocol,不是寫死
`PaperBroker`——這個檔案完全不知道、也不需要知道背後是模擬交易所還是
真實交易所(見 `live/`),只透過 `Broker` 合約的 7 個方法互動。預設值
仍然是 `PaperBroker()`,既有行為完全不變。

比 bot.py 多做的一個泛化:bot.py 一旦進場成交,會「立刻」下出場單。
這裡則是只有在 exit.rule.evaluate(ctx) 為真的時候才下出場單,所以像
MA 交叉策略的止盈止損括號單這種,可以先等訊號觸發,不用一成交就馬上
掛單等。

Phase 2 起,「該不該進場/出場」不再是 plugin 自己手寫的
`should_enter`/`should_exit` 布林方法,而是統一呼叫
`entry.rule.evaluate(ctx)` / `exit.rule.evaluate(ctx)` —— `rule` 是
rules/ 這一層組出來的 Condition 樹(見 strategy_lab/interfaces.py 的
`EntrySignal`/`ExitSignal`)。這個檔案完全不需要知道 Condition 樹長
什麼樣子,只需要知道它有 `.evaluate(ctx) -> bool`。

`kill_switch`(選填)是第四種、跟 `time_window` 平行的「該不該收攤」
判斷,差別只在觸發原因:`time_window` 管排程時間,`kill_switch` 管市場
行為(價格)。`request_stop()`(對應 bot.py 的 `_stop_requested`)是第
三種——外部訊號(SIGINT/SIGTERM)要求停止,不是策略邏輯自己判斷的。
三者都觸發同一個 `_cleanup()`,`tick()` 裡依序檢查,任一個成立就收攤,
不需要分辨是哪一個觸發的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Any, Callable, Dict, Iterator, List, Optional

from loguru import logger

from strategy_lab.broker.paper_broker import PaperBroker
from strategy_lab.engine.events import Event, EventTracker
from strategy_lab.interfaces import Broker, EntrySignal, ExitSignal, KillSwitch, OrderLike, StrategyContext, TimeWindow


class RunState(Enum):
    IDLE = auto()
    ENTRY_PENDING = auto()
    IN_POSITION = auto()
    EXIT_PENDING = auto()
    STOPPED = auto()


@dataclass
class Trade:
    entry_price: float
    exit_price: float
    qty: float
    direction: str = "long"  # "long" 或 "short"——見 docs/ARCHITECTURE.md §6.11

    @property
    def pnl(self) -> float:
        """long 賺的是「賣得比買得貴」(exit - entry);short 賺的是
        「買回得比賣出時便宜」(entry - exit)——兩者符號互為鏡像,直接
        套用 long 那個公式在 short 上會算出正負號相反的錯誤結果。"""
        if self.direction == "short":
            return (self.entry_price - self.exit_price) * self.qty
        return (self.exit_price - self.entry_price) * self.qty


@dataclass
class OrderRecord:
    """每張單的下單/成交/取消,經 on_order 回報(寫進 sl_order 用)。"""

    order_id: str
    purpose: str  # "entry" / "exit" / "forced_close"
    event_index: int  # 屬於第幾個 event(部位 0 → 0 一輪)
    side: str  # "Buy" / "Sell"
    order_type: str  # "limit" / "market"
    price: Optional[float]  # 限價;市價單為 None
    qty: float
    reduce_only: bool
    status: str  # "open" / "closed" / "canceled"
    avg_price: Optional[float]
    filled_qty: float
    time: datetime
    lot: Optional[int] = None  # 分注策略的第幾注(1 起算);非分注策略為 None
    hold_seconds: Optional[float] = None  # 減倉成交時:持倉多久(分注 = 這一注建倉成交起;其他 = 這輪開始有倉起);其他為 None


_ORDER_STATUS_LABEL = {"open": "下單", "closed": "成交", "canceled": "取消"}


def _log_order(rec: OrderRecord) -> None:
    """每張單的下單/成交/取消都寫進 log——實盤原本只有 dry-run 會記,2026-09-27
    那一輪只能靠 Bybit 成交紀錄還原。"""
    lot = f" 第{rec.lot}注" if rec.lot is not None else ""
    head = f"[{_ORDER_STATUS_LABEL.get(rec.status, rec.status)}] event #{rec.event_index}{lot} {rec.purpose} {rec.side} {rec.order_type}"
    if rec.status == "open":
        price = f"@ {rec.price}" if rec.price is not None else "市價"
        logger.info(f"{head} {price} qty={rec.qty:g} reduce_only={rec.reduce_only} id={rec.order_id}")
    elif rec.status == "closed":
        logger.info(f"{head} 均價 {rec.avg_price} 成交量 {rec.filled_qty:g} id={rec.order_id}")
    else:
        logger.info(f"{head} id={rec.order_id}")


@dataclass
class StrategyRunner:
    entry: EntrySignal
    exit: ExitSignal
    time_window: TimeWindow
    order_qty: float
    broker: Broker = field(default_factory=PaperBroker)
    kill_switch: Optional[KillSwitch] = None
    order_type: str = "limit"  # "limit" 或 "market"——見 dsl/order_config.py
    direction: str = "long"  # "long" 或 "short"——見 docs/ARCHITECTURE.md §6.11
    # 重複次數:總 event 數 = loop + 1,做完就收尾結束;None = 不限次數(做到時間窗結束)。
    # 策略 YAML 沒寫時預設 0(dsl/schema.py);直接建構 runner 時預設不限,維持既有行為。
    loop: Optional[int] = None
    on_event: Optional[Callable[[Event], None]] = None
    on_order: Optional[Callable[[OrderRecord], None]] = None

    state: RunState = field(default=RunState.IDLE, init=False)
    window_end: Optional[datetime] = field(default=None, init=False)
    origin_price: Optional[float] = field(default=None, init=False)
    entry_order: Optional[OrderLike] = field(default=None, init=False)
    exit_order: Optional[OrderLike] = field(default=None, init=False)
    active_entry_price: Optional[float] = field(default=None, init=False)
    price_history: List[float] = field(default_factory=list, init=False)
    trades: List[Trade] = field(default_factory=list, init=False)
    entry_time: Optional[datetime] = field(default=None, init=False)
    _stop_requested: bool = field(default=False, init=False)
    # daemon detach(SIGUSR1):直接結束、不收尾,掛單與持倉留在交易所給下一次啟動接手(live/adopt.py)
    detach_requested: bool = field(default=False, init=False)
    events: List[Event] = field(default_factory=list, init=False)
    _tracker: EventTracker = field(default_factory=EventTracker, init=False, repr=False)
    # 停止原因:window_cleanup / kill_switch / stop_requested / loop_done;還在跑時是 None
    stop_reason: Optional[str] = field(default=None, init=False)
    _open_records: Dict[str, OrderRecord] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.loop is not None and self.loop < 0:
            raise ValueError(f"loop 必須是 0 以上的整數或 None(不限次數),目前是 {self.loop}")
        # resting plugin 的 rule 永遠成立,掛單價本身就是觸發條件;市價單
        # 會變成一啟動就市價進場、一成交就市價平倉。
        resting = getattr(self.entry, "resting", False) or getattr(self.exit, "resting", False)
        if resting and self.order_type != "limit":
            raise ValueError(
                f"一啟動就掛單的策略(sat_strategy 機制)只能用 order_type=limit,目前是 {self.order_type!r}"
            )

    # --- 運作中改參數(live/control.py → run_forever 在兩個 tick 之間呼叫)---

    def apply_changes(self, now: datetime, price: float, changes: Dict[str, Any]) -> List[str]:
        """一般策略運作中只能改 loop。先驗證、全部通過才改;回傳給人看的變更說明。"""
        unsupported = sorted(set(changes) - {"loop"})
        if unsupported:
            raise ValueError(f"{unsupported} 只有分注策略可以在運作中改;這個策略只能改 loop")
        if "loop" not in changes:
            return []
        self._validate_loop(changes["loop"])
        return [self._set_loop(changes["loop"])]

    def _validate_loop(self, new: Optional[int]) -> None:
        if new is not None and (isinstance(new, bool) or not isinstance(new, int) or new < 0):
            raise ValueError(f"loop 必須是 0 以上的整數或 null(不限),收到 {new!r}")
        done = len(self.events)
        if new is not None and new + 1 <= done:
            raise ValueError(f"已完成 {done} 個 loop,新的總次數(loop + 1 = {new + 1})要大於 {done}")

    def _set_loop(self, new: Optional[int]) -> str:
        def total(v: Optional[int]) -> str:
            return "不限次數" if v is None else f"共 {v + 1} 個"
        old, self.loop = self.loop, new
        return f"loop {old} → {new}({total(old)} → {total(new)})"

    def request_detach(self) -> None:
        self.detach_requested = True

    def request_stop(self) -> None:
        """對應 sat_strategy/app/bot.py 的 _stop_requested——給外部訊號
        處理器(SIGINT/SIGTERM)呼叫,不是策略邏輯自己決定要停。下一次
        tick() 會觸發 _cleanup(),跟 time_window/kill_switch 待遇一樣:
        取消未成交單、強制平倉,不會留下沒人管的真實掛單或部位。"""
        self._stop_requested = True

    def start(self, now: datetime, price: float) -> None:
        """在整個窗口期間只捕捉一次 origin_price 與 window_end,跟
        bot.py 的 run() 一致(origin_price/entry_price 是在 while 迴圈
        「開始之前」算好的,不會每次進出場循環都重算一次)。"""
        self.origin_price = price
        self.window_end = self.time_window.window_end(now)
        self.state = RunState.IDLE

    def tick(self, now: datetime, price: float) -> None:
        """每收到一個新價格就執行一次，根據目前狀態決定下一步動作。"""

        if not self._prelude(now, price):
            return

        if self.state == RunState.IDLE:
            self._try_enter(now, price)
        elif self.state == RunState.ENTRY_PENDING:
            self._check_entry_fill(now)
        elif self.state == RunState.IN_POSITION:
            self._try_exit(now, price)
        elif self.state == RunState.EXIT_PENDING:
            self._check_exit_fill(now)

    def _prelude(self, now: datetime, price: float) -> bool:
        """每個 tick 共用的前置步驟:記價格、推進 broker、檢查三種收攤條件。
        回傳 False = 已經停止/剛收攤,這個 tick 不用再做事。"""
        if self.state == RunState.STOPPED:
            return False

        self.price_history.append(price)
        self.broker.tick(price)
        self._tracker.on_price(now, price)

        assert self.window_end is not None, "呼叫 tick() 前必須先呼叫 start()"
        # stop_reason 已經有值 = 上一輪收尾做到一半(例如網路失敗)就中斷了,
        # 這一輪直接再收尾一次,不重新判斷——kill switch 的條件可能已經不成立了。
        if self.stop_reason is None:
            if self.time_window.should_cleanup(now, self.window_end):
                self.stop_reason = "window_cleanup"
            elif self.kill_switch is not None and self.kill_switch.rule.evaluate(self._ctx(now, price)):
                self.stop_reason = "kill_switch"
            elif self._stop_requested:
                self.stop_reason = "stop_requested"
            else:
                return True
        self._cleanup(now)
        return False

    def run(
        self,
        now: datetime,
        feed: Iterator[float],
        tick_interval: timedelta,
        max_ticks: int = 100_000,
    ) -> None:
        """給 demo 用的便利迴圈:從一個價格 feed 持續驅動 tick(),直到
        TimeWindow 觸發 cleanup、狀態走到 STOPPED 為止。"""
        price = next(feed)
        self.start(now, price)
        self.tick(now, price)
        for _ in range(max_ticks - 1):
            if self.state == RunState.STOPPED:
                return
            now += tick_interval
            price = next(feed)
            self.tick(now, price)
        if self.state != RunState.STOPPED:
            raise RuntimeError("run() 超過 max_ticks 仍未進入 STOPPED - 請檢查 time_window 設定")

    def _ctx(self, now: datetime, price: float) -> StrategyContext:
        """建立當前策略上下文，供 rule.evaluate() 使用。"""
        return StrategyContext(
            now=now,
            price=price,
            price_history=tuple(self.price_history),
            origin_price=self.origin_price,
            entry_price=self.active_entry_price,
            position_qty=self.broker.position_qty(),
            entry_time=self.entry_time,
            direction=self.direction,
        )

    def _try_enter(self, now: datetime, price: float) -> None:
        """檢查是否滿足進場條件，若滿足則下進場單(限價或市價,依
        order_type;買進開多倉或賣出開空倉,依 direction)。"""

        ctx = self._ctx(now, price)
        if self.entry.rule.evaluate(ctx):
            if self.direction == "short":
                if self.order_type == "market":
                    self.entry_order = self.broker.market_sell(qty=self.order_qty)
                else:
                    self.entry_order = self.broker.limit_sell(price=self.entry.entry_price(ctx), qty=self.order_qty)
            else:
                if self.order_type == "market":
                    self.entry_order = self.broker.market_buy(qty=self.order_qty)
                else:
                    self.entry_order = self.broker.place_limit_buy(price=self.entry.entry_price(ctx), qty=self.order_qty)
            limit_price = self.entry.entry_price(ctx) if self.order_type != "market" else None
            self._emit_order(now, "entry", self.entry_order, self._side(entry=True), self.order_type, limit_price,
                             self.order_qty, reduce_only=False, status="open")
            self.state = RunState.ENTRY_PENDING

    def _check_entry_fill(self, now: datetime) -> None:
        """檢查進場單是否已成交或被取消。"""

        assert self.entry_order is not None
        order = self.broker.fetch_order(self.entry_order.id)
        if order.status == "closed":
            self.active_entry_price = order.price
            self.entry_time = now
            self.state = RunState.IN_POSITION
            self._update_order(now, order.id, "closed", order.price, order.filled_qty)
            self._record_fill(now, self._entry_sign(), order.filled_qty, order.price)
        elif order.status == "canceled":
            self._update_order(now, order.id, "canceled", None, order.filled_qty)
            self.state = RunState.IDLE

    def _try_exit(self, now: datetime, price: float) -> None:
        """檢查是否滿足出場條件，若滿足則下出場單(限價或市價,依
        order_type;平多倉或平空倉,依 direction)。`position_qty()`
        空倉時是負數,平倉數量要取絕對值——不能直接把負數傳給
        qty 參數。"""
        ctx = self._ctx(now, price)
        if self.exit.rule.evaluate(ctx):
            qty = abs(self.broker.position_qty())
            if self.direction == "short":
                if self.order_type == "market":
                    self.exit_order = self.broker.market_flat_sell(qty=qty)
                else:
                    self.exit_order = self.broker.limit_flat_sell(price=self.exit.exit_price(ctx), qty=qty)
            else:
                if self.order_type == "market":
                    self.exit_order = self.broker.market_flat_buy(qty=qty)
                else:
                    self.exit_order = self.broker.place_limit_sell(price=self.exit.exit_price(ctx), qty=qty)
            limit_price = self.exit.exit_price(ctx) if self.order_type != "market" else None
            self._emit_order(now, "exit", self.exit_order, self._side(entry=False), self.order_type, limit_price,
                             qty, reduce_only=True, status="open")
            self.state = RunState.EXIT_PENDING

    def _check_exit_fill(self, now: datetime) -> None:
        """檢查出場單是否已成交或被取消。"""
        assert self.exit_order is not None
        order = self.broker.fetch_order(self.exit_order.id)
        if order.status == "closed":
            assert self.active_entry_price is not None
            self.trades.append(
                Trade(
                    entry_price=self.active_entry_price,
                    exit_price=order.price,
                    qty=order.filled_qty,
                    direction=self.direction,
                )
            )
            self.active_entry_price = None
            self.entry_time = None
            self.state = RunState.IDLE
            self._update_order(now, order.id, "closed", order.price, order.filled_qty)
            self._record_fill(now, -self._entry_sign(), order.filled_qty, order.price)
            if self._loop_exhausted():
                self.stop_reason = "loop_done"
                self._cleanup(now)
        elif order.status == "canceled":
            self._update_order(now, order.id, "canceled", None, order.filled_qty)
            self.state = RunState.IN_POSITION

    def _side(self, entry: bool) -> str:
        buys_on_entry = self.direction != "short"
        return "Buy" if buys_on_entry == entry else "Sell"

    def _emit_order(self, now, purpose, order, side, order_type, price, qty, reduce_only, status,
                    avg_price=None, filled_qty=0.0, lot=None) -> None:
        rec = OrderRecord(
            order_id=order.id, purpose=purpose, event_index=self._tracker.completed + 1, side=side,
            order_type=order_type, price=price, qty=qty, reduce_only=reduce_only, status=status,
            avg_price=avg_price, filled_qty=filled_qty, time=now, lot=lot,
        )
        self._remember(rec)

    def _update_order(self, now, order_id, status, avg_price, filled_qty, hold_seconds=None) -> None:
        prev = self._open_records.get(order_id)
        if prev is None:
            return
        if status == "closed" and hold_seconds is None:
            hold_seconds = self._closing_hold_seconds(now, prev.side)
        rec = OrderRecord(**{**prev.__dict__, "status": status, "avg_price": avg_price, "filled_qty": filled_qty,
                             "time": now, "hold_seconds": hold_seconds})
        self._remember(rec)

    def _closing_hold_seconds(self, now: datetime, side: str) -> Optional[float]:
        """這張成交是減倉(方向跟目前部位相反)→ 從這輪開始有倉算到現在的秒數;建倉單 → None。
        要在 _record_fill 之前呼叫(那時 tracker 還是成交前的部位)。分注策略的平倉單自己傳每注的
        持倉時間;收尾一次平掉好幾注的那張,算的是最早那注(這輪開始)到現在。"""
        since = self._tracker.open_since
        position = self._tracker.position
        if since is None or position == 0 or (position > 0) == (side == "Buy"):
            return None
        return (now - since).total_seconds()

    def _remember(self, rec: OrderRecord) -> None:
        if rec.status == "open":
            self._open_records[rec.order_id] = rec
        else:
            self._open_records.pop(rec.order_id, None)
        _log_order(rec)
        if self.on_order is not None:
            self.on_order(rec)

    def _entry_sign(self) -> int:
        return -1 if self.direction == "short" else 1

    def _loop_exhausted(self) -> bool:
        return self.loop is not None and len(self.events) >= self.loop + 1

    def _record_fill(self, now: datetime, sign: int, qty: float, price: float, forced: bool = False) -> None:
        event = self._tracker.on_fill(now, sign * qty, price, forced=forced)
        if event is None:
            return
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def _cleanup(self, now: datetime) -> None:
        """時間窗口結束時，取消所有未成交單並強制平倉。改用
        market_flat_buy()/market_flat_sell()(不是舊的 market_close())
        是刻意的:`position_qty()` 對空倉會回傳負數,`market_close()`
        原本假設部位一定是正數(`remaining > 0` 才平倉),空倉會被完全
        忽略、永遠不會被強制平倉——見 docs/ARCHITECTURE.md §6.11。
        依部位正負號決定方向,不依賴 self.direction:部位本身才是「現在
        真的有沒有倉位」的唯一事實來源。附帶好處:market_flat_buy()/
        market_flat_sell() 會套用 §6.9 的下單精度修正,market_close()
        原本沒有。"""
        logger.bind(telegram=False).warning(f"開始收尾({self.stop_reason}):取消未成交掛單,市價平掉未平倉部位")
        self._cancel_open_orders(now)
        self._flatten(now)
        self.state = RunState.STOPPED

    def _cancel_open_orders(self, now: datetime) -> None:
        for order in (self.entry_order, self.exit_order):
            if order is not None:
                self.broker.cancel_order(order.id)
                self._update_order(now, order.id, "canceled", None, 0.0)

    def _flatten(self, now: datetime) -> None:
        """依部位正負號市價平倉(見 _cleanup 的說明),並記成 forced 成交。"""
        remaining = self.broker.position_qty()
        if remaining != 0:
            qty = abs(remaining)
            side = "Sell" if remaining > 0 else "Buy"
            if remaining > 0:
                close_order = self.broker.market_flat_buy(qty)
            else:
                close_order = self.broker.market_flat_sell(qty)
            if close_order is not None:
                self._emit_order(now, "forced_close", close_order, side, "market", None, qty, reduce_only=True, status="open")
            price, filled = self._forced_fill(close_order)
            if filled and close_order is not None:
                self._update_order(now, close_order.id, "closed", price, qty)
            self._record_fill(now, -1 if remaining > 0 else 1, qty, price, forced=True)

    def _forced_fill(self, close_order: Optional[OrderLike]):
        """強制平倉的成交價:能查到就用交易所回報的;查不到(還沒成交回報)
        就用最後一個 tick 的價格估算,只影響紀錄,不影響平倉本身。"""
        last = self.price_history[-1] if self.price_history else 0.0
        if close_order is None:
            return last, False
        fetched = self.broker.fetch_order(close_order.id)
        if fetched.status == "closed" and fetched.price:
            return fetched.price, True
        return last, False
