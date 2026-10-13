"""每日對帳(2026-10-13):把 Bybit 上的成交跟 trading DB 比對。

- 情況 1:成交落在某筆紀錄(同帳戶、同幣種)的時間裡,DB 卻沒有 → 自動補進那筆紀錄(sl_fill)
- 情況 2:成交不在任何紀錄的時間裡(全手動交易沒記)→ 不自動補,列出來發警告,請使用者到「補記」分頁補
- 先把 logs/db_pending/ 的待補檔補進 DB(DB 斷線最常見的漏記)
- 警告過的成交記在狀態檔,同一批不重複發
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from strategy_lab.live import free_style as fs
from strategy_lab.live import reconcile as rc
from strategy_lab.storage.models import SlFill, SlRun
from strategy_lab.storage.setup_db import setup

T0 = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(days=1)


def t(minutes):
    return T0 + timedelta(minutes=minutes)


def ms(dt):
    return str(int(dt.timestamp() * 1000))


def ex(eid, symbol, when, side="Buy", qty=1, price=100, fee=0.01, exec_type="Trade", oid=None):
    return {"execId": eid, "orderId": oid or f"o-{eid}", "symbol": symbol, "side": side, "execQty": str(qty), "execPrice": str(price),
            "execFee": str(fee), "isMaker": True, "execType": exec_type, "execTime": ms(when)}


class FakeClient:
    def __init__(self, executions, positions=None):
        self.executions = executions
        self.positions = positions or {}  # 現在的持倉(多正空負)
        self.queries = []

    def get_executions(self, symbol, start, end):
        self.queries.append((symbol, start, end))
        return [e for e in self.executions if (symbol is None or e["symbol"] == symbol)
                and start <= datetime.fromtimestamp(int(e["execTime"]) / 1000, tz=timezone.utc) <= end]

    def get_position_qty(self, symbol):
        return self.positions.get(symbol, 0.0)

    def get_open_orders(self, symbol):
        return []

    def list_order_history(self, symbol, start, end):
        """每筆成交一張已成交的單(補記只收自己訂單的成交)。"""
        return [{"orderId": e["orderId"], "symbol": e["symbol"], "side": e["side"], "orderType": "Limit",
                 "qty": e["execQty"], "price": e["execPrice"], "orderStatus": "Filled", "avgPrice": e["execPrice"],
                 "cumExecQty": e["execQty"], "reduceOnly": False, "createdTime": e["execTime"], "updatedTime": e["execTime"]}
                for e in self.get_executions(symbol, start, end) if e["execType"] == "Trade"]


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'trading.db'}"
    setup(url)
    return url


def fills(db):
    engine = create_engine(db)
    with Session(engine) as s:
        out = {f.exec_id: f for f in s.scalars(select(SlFill))}
    engine.dispose()
    return out


def run_ids(db):
    engine = create_engine(db)
    with Session(engine) as s:
        out = list(s.scalars(select(SlRun.run_id)))
    engine.dispose()
    return out


class TestReconcileAccount:
    def recorded_run(self, db, tmp_path, symbol="ETHUSDT", start=t(0), end=t(120), account=None):
        """一筆已結束的紀錄,裡面已經有成交 e1。"""
        client = FakeClient([ex("e1", symbol, start + timedelta(minutes=5)),
                             ex("e2", symbol, start + timedelta(minutes=10), side="Sell")])
        fs.backfill(db, client, symbol, start, end, NOW, account=account, pending_dir=tmp_path)
        return run_ids(db)[0]

    def test_everything_recorded_means_nothing_to_do(self, db, tmp_path):
        self.recorded_run(db, tmp_path)
        client = FakeClient([ex("e1", "ETHUSDT", t(5)), ex("e2", "ETHUSDT", t(10), side="Sell")])
        report = rc.reconcile_account(db, client, "test1", T0 - timedelta(hours=1), NOW)
        assert report.checked == 2 and report.repaired == [] and report.unrecorded == []
        assert client.queries[0][0] is None  # 不分幣種

    def test_missing_fill_inside_a_recorded_run_is_repaired(self, db, tmp_path):
        run_id = self.recorded_run(db, tmp_path)
        client = FakeClient([ex("e1", "ETHUSDT", t(5)), ex("e2", "ETHUSDT", t(10), side="Sell"),
                             ex("e3", "ETHUSDT", t(60), fee=0.02, oid="o9"),
                             ex("f1", "ETHUSDT", t(70), exec_type="Funding", fee=-0.001)])
        report = rc.reconcile_account(db, client, "test1", T0, NOW)
        [rep] = report.repaired
        assert (rep.symbol, rep.run_id, rep.strategy_name, rep.fills) == ("ETHUSDT", run_id, "free style", 2)
        got = fills(db)
        assert got["e3"].run_id == run_id and got["e3"].order_id == "o9" and float(got["e3"].fee) == 0.02
        assert got["f1"].exec_type == "Funding"
        assert report.unrecorded == []

    def test_trades_outside_every_run_are_unrecorded_and_not_written(self, db, tmp_path):
        self.recorded_run(db, tmp_path)
        client = FakeClient([ex("e1", "ETHUSDT", t(5)), ex("e2", "ETHUSDT", t(10), side="Sell"),
                             ex("m1", "ETHUSDT", t(300)), ex("m2", "ETHUSDT", t(330), side="Sell"),
                             ex("m3", "XRPUSDT", t(400)),
                             ex("fz", "XRPUSDT", t(480), exec_type="Funding")],  # 資金費不算交易
                            positions={"XRPUSDT": 1.0})  # XRP 還沒平倉 = 進行中,先不列
        report = rc.reconcile_account(db, client, "test1", T0, NOW)
        assert [(u.symbol, u.first, u.last, u.count, u.exec_ids) for u in report.unrecorded] == [
            ("ETHUSDT", t(300), t(330), 2, ["m1", "m2"])]
        assert not {"m1", "m2", "m3"} & set(fills(db))

    def test_runs_of_other_accounts_do_not_count(self, db, tmp_path):
        self.recorded_run(db, tmp_path, account="MAIN")
        client = FakeClient([ex("x1", "ETHUSDT", t(30)), ex("x2", "ETHUSDT", t(40), side="Sell")])
        report = rc.reconcile_account(db, client, "test1", T0, NOW)
        assert [u.exec_ids for u in report.unrecorded] == [["x1", "x2"]] and report.repaired == []

    def test_running_run_covers_until_now(self, db, tmp_path):
        fs.start(db, FakeClient([]), "ETHUSDT", t(0), pending_dir=tmp_path)
        report = rc.reconcile_account(db, FakeClient([ex("r1", "ETHUSDT", t(600))]), "test1", T0, NOW)
        assert [r.fills for r in report.repaired] == [1] and report.unrecorded == []

    def test_long_windows_are_queried_in_7_day_chunks(self, db):
        client = FakeClient([])
        rc.reconcile_account(db, client, "test1", NOW - timedelta(days=15), NOW)
        assert len(client.queries) == 3


class TestDailyRun:
    """main():每個帳戶從上次對帳接著查(第一次 7 天),先補 db_pending,情況 1 / 2 都發系統警報,警告過的不重發。"""

    @pytest.fixture
    def env(self, db, tmp_path, monkeypatch):
        from strategy_lab.live import accounts

        envf = tmp_path / ".env"
        envf.write_text("BYBIT_API_KEY=k\nBYBIT_API_SECRET=s\nACCOUNT = MAIN\nBYBIT_MAIN_API_KEY=m\nBYBIT_MAIN_API_SECRET=n\n")
        monkeypatch.setattr(accounts, "ENV_PATH", envf)
        sent = []
        clients = {"test1": FakeClient([ex("m1", "ETHUSDT", NOW - timedelta(hours=2)),
                                        ex("m2", "ETHUSDT", NOW - timedelta(hours=1, minutes=30), side="Sell")]),
                   "MAIN": FakeClient([])}
        monkeypatch.setattr(rc, "make_client", lambda account: clients[account.name])
        monkeypatch.setattr(rc, "send_alert", lambda text: sent.append(text))
        pending = tmp_path / "pending"
        pending.mkdir()
        return {"db": db, "state": tmp_path / "state.json", "pending": pending, "sent": sent, "clients": clients}

    def daily(self, env, now=NOW):
        return rc.run_daily(env["db"], now, state_path=env["state"], pending_dir=env["pending"])

    def test_first_run_looks_back_7_days_and_warns_once(self, env):
        self.daily(env)
        assert env["clients"]["test1"].queries[0][1] == NOW - timedelta(days=7)
        [msg] = env["sent"]
        assert "⚠️" in msg and "test1" in msg and "ETHUSDT" in msg and "2 筆" in msg and "補記" in msg
        state = json.loads(env["state"].read_text())
        assert state["accounts"]["test1"]["last_end"] == NOW.isoformat()
        assert [u["exec_ids"] for u in state["unrecorded"]] == [["m1", "m2"]]

        env["sent"].clear()
        later = NOW + timedelta(days=1)
        env["clients"]["test1"].queries.clear()
        self.daily(env, later)
        assert env["clients"]["test1"].queries[0][1] == NOW  # 從上次對帳接著查
        assert env["sent"] == []  # 同一批不重發(這次也查不到 m1)
        assert [u["exec_ids"] for u in json.loads(env["state"].read_text())["unrecorded"]] == [["m1", "m2"]]  # 還沒補,繼續列著

    def test_unrecorded_list_drops_what_has_been_backfilled(self, env, tmp_path):
        self.daily(env)
        fs.backfill(env["db"], env["clients"]["test1"], "ETHUSDT", NOW - timedelta(hours=3), NOW - timedelta(hours=1),
                    NOW, pending_dir=tmp_path)
        self.daily(env, NOW + timedelta(days=1))
        assert json.loads(env["state"].read_text())["unrecorded"] == []

    def test_pending_files_are_replayed_and_reported(self, env, monkeypatch):
        monkeypatch.setattr(rc, "replay_pending", lambda db, pending_dir: 24)
        self.daily(env)
        assert any("🔧" in m and "24" in m for m in env["sent"])

    def test_nothing_wrong_sends_nothing(self, env):
        env["clients"]["test1"].executions = []
        self.daily(env)
        assert env["sent"] == []

    def test_a_broken_account_is_reported_and_others_still_checked(self, env, monkeypatch):
        def make_client(account):
            if account.name == "MAIN":
                raise RuntimeError("key 失效")
            return env["clients"]["test1"]

        monkeypatch.setattr(rc, "make_client", make_client)
        self.daily(env)
        assert any("MAIN" in m and "key 失效" in m for m in env["sent"])
        assert any("ETHUSDT" in m for m in env["sent"])


class TestRounds:
    """未記錄的交易照使用者的定義分組(2026-10-13):持倉 0 → 0 算一對交易,中間加倉幾次、隔多久都不切。
    查詢起點的持倉 = 現在的持倉 − 這段期間的成交;起點不是 0 就自動往前查到持倉為 0 的那一刻。
    還沒平倉的(進行中)先不列。"""

    def test_users_example_is_one_round_even_with_long_gaps(self, db):
        client = FakeClient([ex("b1", "ETHUSDT", t(0), qty=5), ex("b2", "ETHUSDT", t(60), qty=5),
                             ex("s1", "ETHUSDT", t(60 + 600), side="Sell", qty=5),       # 隔 10 小時
                             ex("s2", "ETHUSDT", t(60 + 600 + 480), side="Sell", qty=5)])  # 再隔 8 小時
        [u] = rc.reconcile_account(db, client, "MAIN", T0, NOW).unrecorded
        assert (u.first, u.last, u.count, u.exec_ids) == (t(0), t(1140), 4, ["b1", "b2", "s1", "s2"])

    def test_back_to_zero_starts_a_new_round(self, db):
        client = FakeClient([ex("a1", "ETHUSDT", t(0)), ex("a2", "ETHUSDT", t(10), side="Sell"),
                             ex("c1", "ETHUSDT", t(20), side="Sell", qty=2), ex("c2", "ETHUSDT", t(30), qty=2)])
        assert [u.exec_ids for u in rc.reconcile_account(db, client, "MAIN", T0, NOW).unrecorded] == [
            ["a1", "a2"], ["c1", "c2"]]

    def test_flip_through_zero_splits_the_fill(self, db):
        """多 1 → 一筆賣 2 = 平多 + 開空:同一筆成交屬於兩對,兩對都列(補記時間各自涵蓋它)"""
        client = FakeClient([ex("f1", "ETHUSDT", t(0)), ex("f2", "ETHUSDT", t(10), side="Sell", qty=2),
                             ex("f3", "ETHUSDT", t(20))])
        assert [(u.first, u.last) for u in rc.reconcile_account(db, client, "MAIN", T0, NOW).unrecorded] == [
            (t(0), t(10)), (t(10), t(20))]

    def test_open_round_at_the_end_is_not_listed_yet(self, db):
        client = FakeClient([ex("a1", "ETHUSDT", t(0)), ex("a2", "ETHUSDT", t(10), side="Sell"),
                             ex("o1", "ETHUSDT", t(20))], positions={"ETHUSDT": 1.0})
        report = rc.reconcile_account(db, client, "MAIN", T0, NOW)
        assert [u.exec_ids for u in report.unrecorded] == [["a1", "a2"]]
        assert [(o.symbol, o.position) for o in report.open_rounds] == [("ETHUSDT", 1.0)]

    def test_round_that_started_before_the_window_is_found_by_looking_back(self, db):
        """查 T0 之後:T0 時持倉已經是 +3(10 天前買的)→ 往前查到買的那一筆,這一對從那裡開始"""
        early = T0 - timedelta(days=10)
        client = FakeClient([ex("old", "ETHUSDT", early, qty=3), ex("s1", "ETHUSDT", t(30), side="Sell", qty=3),
                             ex("n1", "ETHUSDT", t(60)), ex("n2", "ETHUSDT", t(70), side="Sell")])
        report = rc.reconcile_account(db, client, "MAIN", T0, NOW)
        assert [(u.first, u.exec_ids) for u in report.unrecorded] == [(early, ["old", "s1"]), (t(60), ["n1", "n2"])]

    def test_round_partly_recorded_is_not_listed(self, db, tmp_path):
        """一對交易裡全部成交都已有紀錄 → 不列"""
        client = FakeClient([ex("r1", "ETHUSDT", t(5)), ex("r2", "ETHUSDT", t(10), side="Sell")])
        fs.backfill(db, client, "ETHUSDT", T0, t(20), NOW, account="MAIN", pending_dir=tmp_path)
        assert rc.reconcile_account(db, client, "MAIN", T0, NOW).unrecorded == []
