"""free style:不用策略 YAML,使用者自己手動下單;start 只記開始時間,stop 時才去 Bybit
把期間內這個幣種的單、成交明細拉回來寫進 sl_order / sl_fill / sl_event,補上 sl_run 的
結束時間與損益(2026-10-09)。中間不用有程式在跑,Mac 合蓋睡眠不影響。"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from strategy_lab.live import free_style as fs
from strategy_lab.storage.models import SlEvent, SlFill, SlOrder, SlRun
from strategy_lab.storage.setup_db import setup

T0 = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)


def t(minutes):
    return T0 + timedelta(minutes=minutes)


def ms(dt):
    return str(int(dt.timestamp() * 1000))


def order(oid, side, otype, qty, price, status, created, updated, avg=None, filled=0, reduce_only=False):
    return {"orderId": oid, "symbol": "ETHUSDT", "side": side, "orderType": otype, "qty": str(qty),
            "price": str(price) if price is not None else "0", "orderStatus": status, "reduceOnly": reduce_only,
            "avgPrice": str(avg) if avg is not None else "", "cumExecQty": str(filled),
            "createdTime": ms(created), "updatedTime": ms(updated)}


def execution(eid, oid, side, qty, price, fee, when, maker=True, exec_type="Trade"):
    return {"execId": eid, "orderId": oid, "symbol": "ETHUSDT", "side": side, "execQty": str(qty),
            "execPrice": str(price), "execFee": str(fee), "isMaker": maker, "execType": exec_type, "execTime": ms(when)}


class FakeClient:
    def __init__(self, position=0.0, open_orders=None, orders=None, executions=None):
        self.position = position
        self.open_orders = open_orders or []
        self.orders = orders or []
        self.executions = executions or []
        self.calls = []

    def get_position_qty(self, symbol):
        return self.position

    def get_open_orders(self, symbol):
        return [o for o in self.open_orders if o["symbol"] == symbol]

    def list_order_history(self, symbol, start, end):
        self.calls.append(("orders", start, end))
        return [o for o in self.orders if start <= _dt(o["createdTime"]) <= end]

    def get_executions(self, symbol, start, end):
        self.calls.append(("executions", start, end))
        return [e for e in self.executions if start <= _dt(e["execTime"]) <= end]


def _dt(ms_text):
    return datetime.fromtimestamp(int(ms_text) / 1000, tz=timezone.utc)


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'trading.db'}"
    setup(url)
    return url


def rows(url, model):
    engine = create_engine(url)
    with Session(engine) as s:
        result = list(s.scalars(select(model)))
    engine.dispose()
    return result


def two_round_trips():
    """第 1 輪:限價買 0.1 @ 2500 → 限價賣 0.1 @ 2510(毛利 +1.0),中間收一次資金費 0.01;
    一張沒成交被取消的買單;第 2 輪:市價空 0.2 @ 2490 → 市價買回 @ 2480(毛利 +2.0)。"""
    orders = [
        order("o1", "Buy", "Limit", 0.1, 2500, "Filled", t(5), t(10), avg=2500, filled=0.1),
        order("o2", "Sell", "Limit", 0.1, 2510, "Filled", t(11), t(30), avg=2510, filled=0.1, reduce_only=True),
        order("o3", "Buy", "Limit", 0.1, 2400, "Cancelled", t(40), t(45)),
        order("o4", "Sell", "Market", 0.2, None, "Filled", t(50), t(50), avg=2490, filled=0.2),
        order("o5", "Buy", "Market", 0.2, None, "Filled", t(60), t(60), avg=2480, filled=0.2, reduce_only=True),
    ]
    executions = [
        execution("e1", "o1", "Buy", 0.1, 2500, 0.05, t(10)),
        execution("f1", "", "Sell", 0.1, 2505, 0.01, t(20), exec_type="Funding"),
        execution("e2", "o2", "Sell", 0.1, 2510, 0.05, t(30)),
        execution("e3", "o4", "Sell", 0.2, 2490, 0.27, t(50), maker=False),
        execution("e4", "o5", "Buy", 0.2, 2480, 0.27, t(60), maker=False),
    ]
    return orders, executions


class TestStart:
    def test_writes_a_free_style_run_with_only_the_start_time_and_symbol(self, db, tmp_path):
        run_id = fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path)

        [run] = rows(db, SlRun)
        assert run.run_id == run_id
        assert run.strategy_name == "free style"
        assert run.symbol == "ETHUSDT"
        assert run.strategy_path == "NA" and run.strategy_yaml == "NA"
        assert run.direction == "NA" and run.order_type == "NA"
        assert run.strategy_params == {}
        assert run.qty is None and run.loop is None and run.origin_price is None and run.preflight is None
        assert run.testnet is False
        assert run.started_at.replace(tzinfo=timezone.utc) == T0
        assert run.ended_at is None

    def test_refuses_when_the_symbol_already_has_a_running_run(self, db, tmp_path):
        """同一個幣同時只能有一段:另一段 free style 或 strategy_lab 實盤還沒結束,單會混在一起"""
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path)
        with pytest.raises(fs.FreeStyleError, match="還沒結束"):
            fs.start(db, FakeClient(), "ETHUSDT", t(5), pending_dir=tmp_path)
        assert len(rows(db, SlRun)) == 1

    def test_other_symbols_are_independent(self, db, tmp_path):
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path)
        fs.start(db, FakeClient(), "XRPUSDT", T0, pending_dir=tmp_path)
        assert {r.symbol for r in rows(db, SlRun)} == {"ETHUSDT", "XRPUSDT"}

    def test_refuses_when_there_is_already_a_position(self, db, tmp_path):
        """一開始就有倉位,損益會算錯(不知道原本的成本)"""
        with pytest.raises(fs.FreeStyleError, match="持倉"):
            fs.start(db, FakeClient(position=0.3), "ETHUSDT", T0, pending_dir=tmp_path)
        assert rows(db, SlRun) == []

    def test_refuses_when_there_are_open_orders(self, db, tmp_path):
        client = FakeClient(open_orders=[order("x", "Buy", "Limit", 0.1, 2400, "New", t(-60), t(-60))])
        with pytest.raises(fs.FreeStyleError, match="掛單"):
            fs.start(db, client, "ETHUSDT", T0, pending_dir=tmp_path)

    def test_refuses_without_a_database(self, tmp_path):
        """start 只寫一列 sl_run,stop 要靠它找到開始時間;資料庫連不上就不能開始"""
        with pytest.raises(fs.FreeStyleError, match="資料庫"):
            fs.start(None, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path)


class TestStop:
    def test_pulls_orders_fills_and_events_from_bybit_into_the_db(self, db, tmp_path):
        orders, executions = two_round_trips()
        run_id = fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path)

        result = fs.stop(db, FakeClient(orders=orders, executions=executions), "ETHUSDT", t(120), pending_dir=tmp_path)

        db_orders = {o.order_id: o for o in rows(db, SlOrder)}
        assert set(db_orders) == {"o1", "o2", "o3", "o4", "o5"}
        assert all(o.run_id == run_id and o.purpose == "manual" and o.lot is None for o in db_orders.values())
        assert [db_orders[k].event_index for k in ("o1", "o2", "o3", "o4", "o5")] == [1, 1, 2, 2, 2]
        assert db_orders["o1"].status == "closed" and db_orders["o3"].status == "canceled"
        assert db_orders["o4"].order_type == "market" and db_orders["o4"].price is None
        assert db_orders["o2"].reduce_only is True
        assert db_orders["o1"].created_at.replace(tzinfo=timezone.utc) == t(5)
        assert db_orders["o1"].updated_at.replace(tzinfo=timezone.utc) == t(10)

        assert {f.exec_id for f in rows(db, SlFill)} == {"e1", "f1", "e2", "e3", "e4"}

        events = sorted(rows(db, SlEvent), key=lambda e: e.event_index)
        assert [(e.direction, round(e.realized_pnl, 6)) for e in events] == [("long", 1.0), ("short", 2.0)]
        assert round(events[0].net_pnl, 6) == round(1.0 - 0.10 - 0.01, 6)
        assert round(events[1].net_pnl, 6) == round(2.0 - 0.54, 6)

        [run] = rows(db, SlRun)
        assert run.ended_at.replace(tzinfo=timezone.utc) == t(120)
        assert run.end_reason == "free_style_stop"
        assert run.events_count == 2
        assert round(run.gross_pnl, 6) == 3.0
        assert round(run.net_pnl, 6) == round(3.0 - 0.64 - 0.01, 6)

        assert result.run_id == run_id and result.events == 2 and result.orders == 5
        assert round(result.net_pnl, 6) == round(run.net_pnl, 6)
        assert result.open_position == 0

    def test_still_open_orders_are_recorded_as_open(self, db, tmp_path):
        """Bybit 的歷史清單不一定有還掛著的單,另外查 open orders 補上"""
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path)
        resting = order("o9", "Buy", "Limit", 0.1, 2300, "New", t(30), t(30))
        fs.stop(db, FakeClient(open_orders=[resting]), "ETHUSDT", t(120), pending_dir=tmp_path)

        [o] = rows(db, SlOrder)
        assert o.order_id == "o9" and o.status == "open" and o.event_index == 1

    def test_position_still_open_at_stop_is_reported_and_its_round_not_counted(self, db, tmp_path):
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path)
        orders = [order("o1", "Buy", "Limit", 0.1, 2500, "Filled", t(5), t(10), avg=2500, filled=0.1)]
        executions = [execution("e1", "o1", "Buy", 0.1, 2500, 0.05, t(10))]

        result = fs.stop(db, FakeClient(position=0.1, orders=orders, executions=executions), "ETHUSDT", t(60),
                         pending_dir=tmp_path)

        assert result.open_position == 0.1
        assert rows(db, SlEvent) == []
        assert [f.exec_id for f in rows(db, SlFill)] == ["e1"]  # 成交明細照樣寫進去
        [run] = rows(db, SlRun)
        assert run.events_count == 0 and run.ended_at is not None

    def test_ignores_orders_from_before_the_start(self, db, tmp_path):
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path)
        before = order("old", "Buy", "Limit", 0.1, 2500, "Filled", t(-30), t(-20), avg=2500, filled=0.1)
        fs.stop(db, FakeClient(orders=[before]), "ETHUSDT", t(60), pending_dir=tmp_path)
        assert rows(db, SlOrder) == []

    def test_long_sessions_are_queried_in_chunks_of_at_most_7_days(self, db, tmp_path):
        """Bybit 的訂單歷史 / 成交明細一次最多查 7 天"""
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path)
        client = FakeClient()
        fs.stop(db, client, "ETHUSDT", T0 + timedelta(days=20), pending_dir=tmp_path)

        order_calls = [(s, e) for kind, s, e in client.calls if kind == "orders"]
        exec_calls = [(s, e) for kind, s, e in client.calls if kind == "executions"]
        for calls in (order_calls, exec_calls):
            assert calls[0][0] <= T0 and calls[-1][1] >= T0 + timedelta(days=20)
            assert all(e - s <= timedelta(days=7) for s, e in calls)
            assert all(b[0] <= a[1] for a, b in zip(calls, calls[1:]))  # 沒有縫隙

    def test_refuses_when_no_free_style_is_running_for_the_symbol(self, db, tmp_path):
        with pytest.raises(fs.FreeStyleError, match="沒有進行中"):
            fs.stop(db, FakeClient(), "ETHUSDT", t(60), pending_dir=tmp_path)

    def test_a_second_stop_does_nothing(self, db, tmp_path):
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path)
        fs.stop(db, FakeClient(), "ETHUSDT", t(60), pending_dir=tmp_path)
        with pytest.raises(fs.FreeStyleError, match="沒有進行中"):
            fs.stop(db, FakeClient(), "ETHUSDT", t(90), pending_dir=tmp_path)


class TestSummaryMessage:
    def test_summary_shows_rounds_net_pnl_and_open_position(self):
        result = fs.StopResult(run_id="r", symbol="ETHUSDT", started_at=T0, ended_at=t(125), events=2, orders=5,
                               gross_pnl=3.0, fees=0.64, funding=0.01, net_pnl=2.35, open_position=0.1)
        text = fs.summary_message(result)
        assert "free style" in text and "ETHUSDT" in text
        assert "2 輪" in text and "+2.35" in text and "2 小時 5 分" in text
        assert "還有持倉 0.1" in text


class TestAccounts:
    """多帳戶(2026-10-12):free style 記帳戶名稱;「同一個帳戶的同一個幣」只能有一段,不同帳戶可以同時記同一個幣。"""

    def test_start_records_the_account(self, db, tmp_path):
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path, account="MAIN")
        [run] = rows(db, SlRun)
        assert run.account == "MAIN"

    def test_no_account_means_test1(self, db, tmp_path):
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path)
        assert rows(db, SlRun)[0].account == "test1"

    def test_same_symbol_on_two_accounts_is_allowed_but_not_twice_on_one(self, db, tmp_path):
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path, account="MAIN")
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path, account="test1")
        with pytest.raises(fs.FreeStyleError, match="還沒結束"):
            fs.start(db, FakeClient(), "ETHUSDT", t(5), pending_dir=tmp_path, account="main")  # 不分大小寫

    def test_stop_ends_only_that_accounts_run(self, db, tmp_path):
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path, account="MAIN")
        fs.start(db, FakeClient(), "ETHUSDT", T0, pending_dir=tmp_path, account="test1")
        result = fs.stop(db, FakeClient(), "ETHUSDT", t(60), pending_dir=tmp_path, account="MAIN")
        runs = {r.account: r for r in rows(db, SlRun)}
        assert runs["MAIN"].ended_at is not None and runs["test1"].ended_at is None
        assert result.account == "MAIN"


def test_summary_names_the_account():
    result = fs.StopResult(run_id="r", symbol="ETHUSDT", started_at=T0, ended_at=t(60), events=1, orders=2,
                           gross_pnl=1.0, fees=0.1, funding=0.0, net_pnl=0.9, open_position=0, account="MAIN")
    assert "帳戶 MAIN" in fs.summary_message(result)


def test_cli_account_option_uses_that_accounts_keys(monkeypatch, tmp_path):
    from strategy_lab.live import accounts

    env = tmp_path / ".env"
    env.write_text("ACCOUNT = MAIN\nBYBIT_MAIN_API_KEY=mainkey\nBYBIT_MAIN_API_SECRET=mainsecret\n")
    monkeypatch.setattr(accounts, "ENV_PATH", env)
    monkeypatch.setattr(fs, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("TRADING_DB_URL", "sqlite://")
    seen = {}
    monkeypatch.setattr(fs, "start", lambda db, client, symbol, when, **k: seen.update(
        key=client._http.api_key, account=k.get("account"), symbol=symbol) or "run-1")
    assert fs.main(["start", "--symbol", "ethusdt", "--account", "main"]) == 0
    assert seen == {"key": "mainkey", "account": "main", "symbol": "ETHUSDT"}


class TestBackfill:
    """補記過去一段(2026-10-13):選帳戶、幣種、開始與結束(都在過去)、策略名稱(預設 free style),
    從 Bybit 拉回那段期間的單與成交,先預覽、確認後一次寫進 DB(sl_run.end_reason = backfill)。"""

    NOW = t(24 * 60)

    def client(self):
        orders, executions = two_round_trips()
        return FakeClient(orders=orders, executions=executions)

    def test_preview_counts_without_writing(self, db):
        p = fs.preview_backfill(db, self.client(), "ETHUSDT", T0, t(90), self.NOW)
        assert (p.orders, p.events, p.gross_pnl, p.open_position) == (5, 2, pytest.approx(3.0), 0)
        assert p.strategy_name == "free style" and p.account == "test1" and p.warnings == []
        assert rows(db, SlRun) == []

    def test_backfill_writes_one_finished_run_with_the_chosen_window_and_name(self, db, tmp_path):
        result = fs.backfill(db, self.client(), "ETHUSDT", T0, t(90), self.NOW, account="MAIN",
                             strategy_name="sat_weekend", pending_dir=tmp_path)
        [run] = rows(db, SlRun)
        assert run.strategy_name == "sat_weekend" and run.account == "MAIN" and run.end_reason == "backfill"
        assert run.started_at.replace(tzinfo=timezone.utc) == T0 and run.ended_at.replace(tzinfo=timezone.utc) == t(90)
        assert len(rows(db, SlOrder)) >= 5 and len(rows(db, SlEvent)) == 2 and len(rows(db, SlFill)) >= 4
        assert {e.strategy_name for e in rows(db, SlEvent)} == {"sat_weekend"}
        assert result.events == 2 and result.net_pnl == pytest.approx(3.0 - result.fees - result.funding)

    def test_only_the_chosen_window_is_used(self, db):
        p = fs.preview_backfill(db, self.client(), "ETHUSDT", T0, t(35), self.NOW)  # 只有第 1 輪
        assert (p.events, p.gross_pnl) == (1, pytest.approx(1.0))

    @pytest.mark.parametrize("start,end,match", [
        (t(90), T0, "開始要早於結束"),
        (T0, t(24 * 60 + 1), "結束不能晚於現在"),
        (T0 - timedelta(days=731), t(90), "2 年"),
    ])
    def test_bad_windows_are_refused(self, db, start, end, match):
        with pytest.raises(fs.FreeStyleError, match=match):
            fs.preview_backfill(db, self.client(), "ETHUSDT", start, end, self.NOW)

    def test_strategy_name_is_required(self, db):
        with pytest.raises(fs.FreeStyleError, match="策略名稱"):
            fs.preview_backfill(db, self.client(), "ETHUSDT", T0, t(90), self.NOW, strategy_name="  ")

    def test_overlapping_an_existing_run_on_the_same_account_is_refused(self, db, tmp_path):
        """同帳戶同幣種、時間重疊的紀錄 → 成交會被算兩次;別的帳戶不算重疊"""
        fs.backfill(db, self.client(), "ETHUSDT", T0, t(35), self.NOW, pending_dir=tmp_path)
        with pytest.raises(fs.FreeStyleError, match="重疊"):
            fs.preview_backfill(db, self.client(), "ETHUSDT", t(30), t(90), self.NOW)
        fs.preview_backfill(db, self.client(), "ETHUSDT", t(30), t(90), self.NOW, account="MAIN")
        fs.preview_backfill(db, self.client(), "ETHUSDT", t(36), t(90), self.NOW)  # 接在後面不算重疊

    def test_unfinished_run_overlaps_until_now(self, db, tmp_path):
        fs.start(db, FakeClient(), "ETHUSDT", t(100), pending_dir=tmp_path)
        with pytest.raises(fs.FreeStyleError, match="重疊"):
            fs.preview_backfill(db, self.client(), "ETHUSDT", t(200), t(300), self.NOW)

    def test_position_already_open_at_the_start_is_refused(self, db):
        """第一筆成交就是平倉(Bybit closedSize > 0)= 開始前已經有持倉,算不出成本"""
        client = self.client()
        client.executions[0]["closedSize"] = "0.1"
        with pytest.raises(fs.FreeStyleError, match="開始時已經有持倉"):
            fs.preview_backfill(db, client, "ETHUSDT", T0, t(90), self.NOW)

    def test_position_left_at_the_end_is_a_warning(self, db):
        p = fs.preview_backfill(db, self.client(), "ETHUSDT", T0, t(55), self.NOW)  # 第 2 輪只開了空單
        assert p.open_position == pytest.approx(-0.2) and p.events == 1
        assert any("結束時還有持倉" in w for w in p.warnings)

    def test_empty_window_is_refused(self, db):
        with pytest.raises(fs.FreeStyleError, match="沒有交易"):
            fs.preview_backfill(db, FakeClient(), "ETHUSDT", T0, t(90), self.NOW)

    def test_cli_backfill(self, db, monkeypatch, tmp_path):
        from strategy_lab.live import accounts

        env = tmp_path / ".env"
        env.write_text("BYBIT_API_KEY=k\nBYBIT_API_SECRET=s\n")
        monkeypatch.setattr(accounts, "ENV_PATH", env)
        monkeypatch.setattr(fs, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setenv("TRADING_DB_URL", db)
        seen = {}
        monkeypatch.setattr(fs, "backfill", lambda db_, client, symbol, start, end, now, **k: seen.update(
            symbol=symbol, start=start, end=end, **k) or "RESULT")
        monkeypatch.setattr(fs, "summary_message", lambda r: f"總結 {r}")
        monkeypatch.setattr(fs, "send_telegram", lambda text: None)
        assert fs.main(["backfill", "--symbol", "ethusdt", "--start", "2026-10-09 10:00", "--end", "2026-10-09 11:30",
                        "--name", "sat_weekend"]) == 0
        assert seen["symbol"] == "ETHUSDT" and seen["strategy_name"] == "sat_weekend" and seen["account"] is None
        assert (seen["start"], seen["end"]) == (T0, t(90))  # HKT 10:00 = UTC 02:00


def test_backfill_summary_names_the_strategy_and_says_backfill(db, tmp_path):
    orders, executions = two_round_trips()
    result = fs.backfill(db, FakeClient(orders=orders, executions=executions), "ETHUSDT", T0, t(90), t(24 * 60),
                         strategy_name="sat_weekend", pending_dir=tmp_path)
    text = fs.summary_message(result)
    assert text.splitlines()[0] == "📝 sat_weekend 補記|ETHUSDT|帳戶 test1"


class TestBackfillUserFields:
    """補記時可以事後填:預計持倉時間、方向、備註(2026-10-13,使用者選 B:只寫自己確定的值)。
    都是選填;填了寫進 sl_run(預計持倉時間也寫到每一輪 sl_event),config.user_filled 標明是事後補填。"""

    NOW = t(24 * 60)

    def write(self, db, tmp_path, **k):
        orders, executions = two_round_trips()
        return fs.backfill(db, FakeClient(orders=orders, executions=executions), "ETHUSDT", T0, t(90), self.NOW,
                           pending_dir=tmp_path, **k)

    def test_filled_values_are_written_and_marked(self, db, tmp_path):
        self.write(db, tmp_path, expected_hold=timedelta(hours=4), direction="both", note="  週末試手感  ")
        [run] = rows(db, SlRun)
        assert (run.expected_hold_seconds, run.direction, run.note) == (4 * 3600, "both", "週末試手感")
        assert run.config["user_filled"] == ["expected_hold", "direction", "note"]
        assert {e.expected_hold_seconds for e in rows(db, SlEvent)} == {4 * 3600}

    def test_nothing_filled_stays_empty(self, db, tmp_path):
        self.write(db, tmp_path)
        [run] = rows(db, SlRun)
        assert (run.expected_hold_seconds, run.direction, run.note) == (None, "NA", None)
        assert run.config["user_filled"] == []

    @pytest.mark.parametrize("k,match", [
        ({"direction": "up"}, "方向"),
        ({"expected_hold": timedelta(0)}, "預計持倉時間"),
        ({"note": "x" * 501}, "備註"),
    ])
    def test_bad_values_are_refused_at_preview(self, db, k, match):
        orders, executions = two_round_trips()
        with pytest.raises(fs.FreeStyleError, match=match):
            fs.preview_backfill(db, FakeClient(orders=orders, executions=executions), "ETHUSDT", T0, t(90), self.NOW, **k)

    @pytest.mark.parametrize("text,expected", [("90m", timedelta(minutes=90)), ("4h", timedelta(hours=4)),
                                               ("1.5d", timedelta(days=1.5)), ("", None)])
    def test_parse_hold(self, text, expected):
        assert fs.parse_hold(text) == expected

    def test_parse_hold_rejects_garbage(self):
        with pytest.raises(fs.FreeStyleError, match="預計持倉時間"):
            fs.parse_hold("4 hours")

    def test_cli_passes_the_fields(self, db, monkeypatch, tmp_path):
        from strategy_lab.live import accounts

        env = tmp_path / ".env"
        env.write_text("BYBIT_API_KEY=k\nBYBIT_API_SECRET=s\n")
        monkeypatch.setattr(accounts, "ENV_PATH", env)
        monkeypatch.setattr(fs, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setenv("TRADING_DB_URL", db)
        seen = {}
        monkeypatch.setattr(fs, "backfill", lambda *a, **k: seen.update(k) or "RESULT")
        monkeypatch.setattr(fs, "summary_message", lambda r: "總結")
        monkeypatch.setattr(fs, "send_telegram", lambda text: None)
        assert fs.main(["backfill", "--symbol", "eth", "--start", "2026-10-09 10:00", "--end", "2026-10-09 11:30",
                        "--hold", "4h", "--direction", "long", "--note", "測試"]) == 0
        assert (seen["expected_hold"], seen["direction"], seen["note"]) == (timedelta(hours=4), "long", "測試")


class TestSegments:
    """補記的紅字(2026-10-13):同帳戶同幣種已有的紀錄時段(不分策略),開始 / 結束落在哪一段就標「重疊」。
    只查 DB,不碰 Bybit,所以畫面一改就能馬上顯示。"""

    NOW = t(24 * 60)

    def setup_runs(self, db, tmp_path):
        orders, executions = two_round_trips()
        fs.backfill(db, FakeClient(orders=orders, executions=executions), "ETHUSDT", T0, t(90), self.NOW,
                    pending_dir=tmp_path)  # 已結束:T0 → t(90)
        fs.start(db, FakeClient(), "ETHUSDT", t(600), pending_dir=tmp_path)  # 進行中:t(600) → 現在

    def test_segments_of_this_account_and_symbol_oldest_first(self, db, tmp_path):
        self.setup_runs(db, tmp_path)
        fs.start(db, FakeClient(), "BTCUSDT", t(5), pending_dir=tmp_path)  # 別的幣
        segs = fs.existing_segments(db, "ETHUSDT", None, self.NOW)
        assert [(s.strategy_name, s.start, s.end, s.running) for s in segs] == [
            ("free style", T0, t(90), False), ("free style", t(600), self.NOW, True)]
        assert fs.existing_segments(db, "ETHUSDT", "MAIN", self.NOW) == []  # 別的帳戶

    def test_marks_start_end_and_windows_that_swallow_a_segment(self, db, tmp_path):
        self.setup_runs(db, tmp_path)
        segs = fs.existing_segments(db, "ETHUSDT", None, self.NOW)
        assert fs.overlap_marks(segs, t(30), t(120)) == {"start": segs[0], "end": None, "inside": []}
        assert fs.overlap_marks(segs, t(100), t(700)) == {"start": None, "end": segs[1], "inside": []}
        assert fs.overlap_marks(segs, t(95), t(500)) == {"start": None, "end": None, "inside": []}
        assert fs.overlap_marks(segs, t(-10), t(100)) == {"start": None, "end": None, "inside": [segs[0]]}
        assert fs.overlap_marks(segs, t(90), t(600)) == {"start": None, "end": None, "inside": []}  # 剛好接上不算

    def test_cli_symbol_only_needs_the_coin(self, db, monkeypatch, tmp_path):
        from strategy_lab.live import accounts

        env = tmp_path / ".env"
        env.write_text("BYBIT_API_KEY=k\nBYBIT_API_SECRET=s\n")
        monkeypatch.setattr(accounts, "ENV_PATH", env)
        monkeypatch.setattr(fs, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setenv("TRADING_DB_URL", db)
        seen = {}
        monkeypatch.setattr(fs, "start", lambda db_, client, symbol, when, **k: seen.update(symbol=symbol) or "run-1")
        assert fs.main(["start", "--symbol", "eth"]) == 0
        assert seen["symbol"] == "ETHUSDT"
