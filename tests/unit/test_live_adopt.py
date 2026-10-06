"""接手現有持倉(live/adopt.py):把交易所上的持倉與掛單對應回分注策略的每一注,不平倉直接接手。
對不上就拒絕(AdoptionError),絕不亂猜。PaperBroker / 假資料,不碰網路。"""

from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.broker.paper_broker import PaperBroker
from strategy_lab.engine.runner import RunState
from strategy_lab.engine.scale_in_runner import ScaleInRunner
from strategy_lab.live.adopt import AdoptionError, apply_adoption, plan_adoption
from strategy_lab.plugins.entry.scale_in import ScaleInEntry
from strategy_lab.plugins.exit.scale_out import ScaleOutExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

NOW = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)
ENTRY = [1.2297, 1.2291, 1.2285]
QTYS = [20.0, 30.0, 10.0]
EXIT = ScaleOutExit(distance={"value": 0.2, "unit": "pct"})


def order(oid, side, price, qty, reduce_only):
    return {"orderId": oid, "side": side, "price": str(price), "qty": str(qty), "reduceOnly": reduce_only,
            "orderStatus": "New", "orderType": "Limit"}


# 2026-10-06 SUI 的真實情況:三注都成交、三張平倉單掛著(交易所修整過的價格)
SUI_ORDERS = [order("x1", "Sell", 1.2322, 20, True), order("x2", "Sell", 1.2316, 30, True),
              order("x3", "Sell", 1.2310, 10, True)]


def plan(orders, position, avg=1.2254, direction="long", entry=ENTRY):
    return plan_adoption(entry, QTYS, EXIT, direction, orders, position, avg)


class TestPlan:
    def test_all_three_lots_held_match_their_exit_orders(self):
        p = plan(SUI_ORDERS, 60.0)
        assert [(h.lot, h.order_id) for h in p.held] == [(1, "x1"), (2, "x2"), (3, "x3")]
        assert p.pending_entry is None and p.position == 60.0 and p.avg_price == 1.2254
        assert "第1注" in p.describe() and "60" in p.describe()

    def test_first_lot_held_and_second_lot_entry_resting(self):
        p = plan([order("x1", "Sell", 1.2322, 20, True), order("e2", "Buy", 1.2291, 30, False)], 20.0, avg=1.2297)
        assert [h.lot for h in p.held] == [1]
        assert (p.pending_entry.lot, p.pending_entry.order_id) == (2, "e2")

    def test_only_first_entry_resting_and_no_position(self):
        p = plan([order("e1", "Buy", 1.2297, 20, False)], 0.0, avg=0.0)
        assert p.held == [] and p.pending_entry.lot == 1

    def test_position_not_matching_the_lots_is_refused(self):
        with pytest.raises(AdoptionError, match="持倉"):
            plan(SUI_ORDERS, 70.0)

    def test_unknown_order_is_refused(self):
        with pytest.raises(AdoptionError, match="認不出"):
            plan(SUI_ORDERS + [order("zz", "Sell", 1.30, 5, True)], 60.0)

    def test_held_lots_must_be_in_sequence(self):
        with pytest.raises(AdoptionError, match="依序"):
            plan([order("x2", "Sell", 1.2316, 30, True)], 30.0)  # 只有第二注,沒有第一注

    def test_position_without_exit_orders_is_refused(self):
        with pytest.raises(AdoptionError, match="平倉單"):
            plan([], 60.0)

    def test_short_direction(self):
        entry = [1.2297, 1.2303, 1.2309]
        exits = [order(f"x{i}", "Buy", round(p * 0.998, 4), q, True) for i, (p, q) in enumerate(zip(entry, QTYS), 1)]
        p = plan(exits, -60.0, avg=1.2302, direction="short", entry=entry)
        assert [h.lot for h in p.held] == [1, 2, 3]


def runner_with_exchange_state():
    """模擬「舊程式脫離後」的交易所:PaperBroker 裡已經有 60 SUI 持倉和三張平倉單。"""
    broker = PaperBroker()
    broker.tick(1.2254)
    broker.market_buy(60.0)  # 持倉 60
    ids = [broker.place_limit_sell(price=p, qty=q).id for p, q in [(1.2322, 20.0), (1.2316, 30.0), (1.2310, 10.0)]]
    records = []
    runner = ScaleInRunner(
        entry=ScaleInEntry(weights=[2, 3, 1]), exit=EXIT,
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=20.0, entry_prices=ENTRY, loop=2, broker=broker, on_order=records.append,
    )
    runner.start(NOW, 1.1863)
    orders = [order(i, "Sell", p, q, True) for i, (p, q) in zip(ids, [(1.2322, 20), (1.2316, 30), (1.2310, 10)])]
    return runner, broker, orders, records


class TestApply:
    def test_adopted_runner_holds_the_lots_and_does_not_place_or_cancel_anything(self):
        runner, broker, orders, records = runner_with_exchange_state()
        before = len(broker._orders)
        apply_adoption(runner, plan(orders, 60.0), NOW)

        assert runner.state == RunState.IN_POSITION
        assert [l.holding for l in runner.lots] == [True, True, True]
        runner.tick(NOW + timedelta(minutes=1), 1.1863)
        assert len(broker._orders) == before  # 沒有重掛、沒有新單
        assert broker.position_qty() == 60.0

    def test_loop_completes_when_exits_fill_with_pnl_from_exchange_average(self):
        runner, broker, orders, records = runner_with_exchange_state()
        apply_adoption(runner, plan(orders, 60.0), NOW)
        runner.tick(NOW + timedelta(minutes=1), 1.2330)  # 三張平倉單都成交
        assert len(runner.events) == 1
        expected = (1.2322 - 1.2254) * 20 + (1.2316 - 1.2254) * 30 + (1.2310 - 1.2254) * 10
        assert runner.events[0].realized_pnl == pytest.approx(expected)
        assert runner.state == RunState.IDLE  # loop 結束,下一輪從第一注重新開始


# ---------------------------------------------------------------- 接進 live/main.py

class FakeClient:
    def __init__(self, orders=None, position=0.0, avg=0.0):
        self.orders, self.position, self.avg = orders or [], position, avg

    def get_open_orders(self, symbol):
        return self.orders

    def get_position_qty(self, symbol):
        return self.position

    def get_position_avg_price(self, symbol):
        return self.avg


class TestEnsureCleanStartWithAdopt:
    def test_leftovers_are_returned_instead_of_refused_when_adopt_is_on(self):
        from strategy_lab.live.main import ensure_clean_start

        client = FakeClient(SUI_ORDERS, 60.0)
        assert ensure_clean_start(client, "SUIUSDT", dry_run=False, adopt=True) == (SUI_ORDERS, 60.0)
        assert ensure_clean_start(FakeClient(), "SUIUSDT", dry_run=False, adopt=True) is None

    def test_still_refused_without_adopt_and_the_message_mentions_the_option(self):
        from strategy_lab.live.main import LeftoverExchangeStateError, ensure_clean_start

        with pytest.raises(LeftoverExchangeStateError, match="adopt_existing_position"):
            ensure_clean_start(FakeClient(SUI_ORDERS, 60.0), "SUIUSDT", dry_run=False)


class TestAdoptExisting:
    def test_scale_in_runner_takes_over_the_exchange_state(self):
        from strategy_lab.live.main import adopt_existing

        runner, broker, orders, records = runner_with_exchange_state()
        messages = adopt_existing(runner, FakeClient(orders, 60.0, 1.2254), "SUIUSDT", orders, 60.0, NOW)
        assert runner.state == RunState.IN_POSITION and any("第3注" in m for m in messages)

    def test_mismatch_becomes_a_refusal(self):
        from strategy_lab.live.main import LeftoverExchangeStateError, adopt_existing

        runner, *_ = runner_with_exchange_state()
        with pytest.raises(LeftoverExchangeStateError, match="持倉對不上"):
            adopt_existing(runner, FakeClient(SUI_ORDERS, 70.0, 1.2254), "SUIUSDT", SUI_ORDERS, 70.0, NOW)

    def test_non_scale_in_strategy_cannot_adopt(self):
        from strategy_lab.engine.runner import StrategyRunner
        from strategy_lab.live.main import LeftoverExchangeStateError, adopt_existing
        from strategy_lab.plugins.entry.resting_deviation_from_reference import RestingDeviationFromReferenceEntry
        from strategy_lab.plugins.exit.resting_return_to_reference import RestingReturnToReferenceExit

        runner = StrategyRunner(entry=RestingDeviationFromReferenceEntry(deviation_pct=1.0),
                                exit=RestingReturnToReferenceExit(), time_window=WeeklyWindow(), order_qty=1.0)
        with pytest.raises(LeftoverExchangeStateError, match="分注"):
            adopt_existing(runner, FakeClient(SUI_ORDERS, 60.0, 1.2), "SUIUSDT", SUI_ORDERS, 60.0, NOW)


class TestDetach:
    def test_run_forever_exits_without_cleanup_and_leaves_orders(self, monkeypatch):
        from loguru import logger

        import strategy_lab.live.main as main_module
        from strategy_lab.live.config import ExecutionConfig
        from strategy_lab.live.main import build_runner_and_symbol, run_forever
        from strategy_lab.log.telegram_notifier import telegram_filter

        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config = ExecutionConfig(strategy_path="strategies/weekend_mean_reversion.yaml", dry_run=True, poll_interval_seconds=0)
        runner, symbol = build_runner_and_symbol(config)
        clock = {"now": NOW, "n": 0}

        def fake_get_price(runner, config, symbol):
            clock["now"] += timedelta(minutes=5)
            clock["n"] += 1
            if clock["n"] == 3:
                runner.request_detach()  # daemon detach 送的 SIGUSR1
            return 1000.0

        sent = []
        sink = logger.add(lambda m: sent.append(m.record["message"]), level="DEBUG", filter=telegram_filter)
        monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
        monkeypatch.setattr(main_module, "get_current_price", fake_get_price)
        try:
            run_forever(runner, config, symbol, now_fn=lambda: clock["now"])
        finally:
            logger.remove(sink)

        assert runner.state != RunState.STOPPED and runner.stop_reason is None  # 沒有收尾
        assert runner.broker.fetch_order(runner.entry_order.id).status == "open"  # 掛單還在
        assert any("脫離" in m for m in sent) and not any("🏁" in m for m in sent)


class TestPreflightAdoptionPreview:
    def _run(self, tmp_path, monkeypatch, adopt, orders, position, avg=1.2254):
        from strategy_lab.live import preflight

        monkeypatch.setattr(preflight, "SNAPSHOT_DIR", tmp_path / "run")
        monkeypatch.setattr(preflight, "load_dotenv", lambda: None)
        config = tmp_path / "live_sui.yaml"
        config.write_text(
            "strategy_path: strategies/scale_in_ladder.yaml\nsymbol_override: SUIUSDT\n"
            "entry_prices: [1.2297, 1.2291, 1.2285]\ndry_run: false\ntestnet: false\n"
            f"adopt_existing_position: {'true' if adopt else 'false'}\n"
            "strategy_overrides:\n  exit_distance: {value: 0.2, unit: pct}\n"
            "position_sizing:\n  mode: fixed_qty\n  value: 20\n", encoding="utf-8")

        class Client(FakeClient):
            def get_last_price(self, symbol): return 1.1863
            def get_account_equity(self): return 57.26
            def get_fee_rates(self, symbol): return 0.0002, 0.00055
            def get_leverage(self, symbol): return 10.0
            def get_margin_mode(self): return "REGULAR_MARGIN"

        printed, started = [], []
        code = preflight.main(["--config", str(config)], client_factory=lambda c: Client(orders, position, avg),
                              input_fn=lambda p: "no", print_fn=lambda *a: printed.append(" ".join(map(str, a))),
                              start_fn=lambda p: started.append(p), now_fn=lambda: datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc))
        return code, "\n".join(printed)

    def test_adopt_shows_what_will_be_taken_over(self, tmp_path, monkeypatch):
        code, out = self._run(tmp_path, monkeypatch, True, SUI_ORDERS, 60.0)
        assert code == 0 and "接手" in out and "第1注 已持有 20" in out and "不會平倉" in out

    def test_without_adopt_leftovers_are_refused_with_a_hint(self, tmp_path, monkeypatch):
        code, out = self._run(tmp_path, monkeypatch, False, SUI_ORDERS, 60.0)
        assert code == 1 and "adopt_existing_position" in out

    def test_adopt_mismatch_is_refused(self, tmp_path, monkeypatch):
        code, out = self._run(tmp_path, monkeypatch, True, SUI_ORDERS, 70.0)
        assert code == 1 and "持倉對不上" in out
