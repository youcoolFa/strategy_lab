"""交易紀錄寫進資料庫:sl_run / sl_order / sl_fill / sl_event。測試用臨時
SQLite;正式環境是 Postgres 的 trading 資料庫(TRADING_DB_URL)。資料庫掛掉
不能影響交易——第一次寫入失敗後,這次執行剩下的紀錄都改寫本機檔案,之後
用 backfill 補進資料庫。"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from strategy_lab.engine.events import Event
from strategy_lab.engine.runner import OrderRecord
from strategy_lab.storage.backfill import backfill
from strategy_lab.storage.models import Base, SlEvent, SlFill, SlOrder, SlRun
from strategy_lab.storage.recorder import RunInfo, TradeRecorder

T0 = datetime(2026, 10, 4, 4, 0, tzinfo=timezone.utc)


def t(minutes):
    return T0 + timedelta(minutes=minutes)


def run_info(**overrides):
    base = dict(
        strategy_name="weekend_band_reversion", strategy_path="strategies/weekend_band_reversion.yaml",
        strategy_yaml="name: weekend_band_reversion\n", strategy_params={"direction": "long"},
        config={"symbol_override": "BTCUSDT", "dry_run": False}, symbol="BTCUSDT", category="linear",
        direction="long", origin_price=1000.0, origin_source="手動輸入", qty=1.0, order_type="limit",
        loop=None, testnet=False, preflight=None, log_path="logs/x.log", started_at=T0,
    )
    base.update(overrides)
    return RunInfo(**base)


def order(order_id, purpose, side, status, event_index=1, price=990.0, avg=None, filled=0.0, at=0):
    return OrderRecord(
        order_id=order_id, purpose=purpose, event_index=event_index, side=side, order_type="limit",
        price=price, qty=1.0, reduce_only=(purpose != "entry"), status=status,
        avg_price=avg, filled_qty=filled, time=t(at),
    )


def execution(exec_id, order_id, side, price, fee, exec_type="Trade", maker=True, at=0):
    return {
        "execId": exec_id, "orderId": order_id, "side": side, "execPrice": str(price), "execQty": "1",
        "execFee": str(fee), "execType": exec_type, "isMaker": maker,
        "execTime": str(int(t(at).timestamp() * 1000)),
    }


def event(index=1, pnl=20.0, start=0, end=30, forced=False):
    return Event(
        index=index, direction="long", start_time=t(start), end_time=t(end), fills=2, max_position=1.0,
        avg_entry=990.0, avg_exit=1010.0, realized_pnl=pnl, max_drawdown=-5.0, forced=forced,
    )


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'trading.db'}"
    Base.metadata.create_all(create_engine(url))
    return url


def rows(url, model):
    with Session(create_engine(url)) as s:
        return list(s.scalars(select(model)))


class FakeExecutions:
    def __init__(self, items):
        self.items, self.calls = items, []

    def __call__(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        return [e for e in self.items if start <= datetime.fromtimestamp(int(e["execTime"]) / 1000, timezone.utc) <= end]


def make_recorder(url, tmp_path, executions=None):
    return TradeRecorder(db_url=url, pending_dir=tmp_path / "pending", fetch_executions=executions or FakeExecutions([]))


class TestTableNames:
    def test_four_tables_with_sl_prefix(self):
        assert set(Base.metadata.tables) == {"sl_run", "sl_order", "sl_fill", "sl_event"}


class TestRunLifecycle:
    def test_start_and_end_run(self, db, tmp_path):
        rec = make_recorder(db, tmp_path)
        run_id = rec.start_run(run_info())
        rec.end_run(t(60), "window_cleanup")

        [run] = rows(db, SlRun)
        assert run.run_id == run_id
        assert run.strategy_name == "weekend_band_reversion"
        assert run.config["symbol_override"] == "BTCUSDT"
        assert run.end_reason == "window_cleanup"
        assert run.ended_at is not None
        assert run.python_executable  # 之前查不到用哪個 Python,現在記下來

    def test_config_snapshot_never_contains_secrets(self, db, tmp_path):
        rec = make_recorder(db, tmp_path)
        rec.start_run(run_info(config={"symbol_override": "BTCUSDT", "api_secret": "x", "BYBIT_API_KEY": "y"}))
        [run] = rows(db, SlRun)
        assert "api_secret" not in run.config and "BYBIT_API_KEY" not in run.config


class TestOrders:
    def test_order_is_upserted_by_exchange_order_id(self, db, tmp_path):
        rec = make_recorder(db, tmp_path)
        rec.start_run(run_info())
        rec.record_order(order("o1", "entry", "Buy", "open", at=0))
        rec.record_order(order("o1", "entry", "Buy", "closed", avg=989.5, filled=1.0, at=1))

        [o] = rows(db, SlOrder)
        assert o.status == "closed"
        assert float(o.avg_price) == pytest.approx(989.5)
        assert o.purpose == "entry" and o.event_index == 1


class TestEvents:
    def test_event_net_pnl_uses_real_fees_and_funding_from_exchange(self, db, tmp_path):
        executions = FakeExecutions([
            execution("e1", "o1", "Buy", 990, 0.198, at=1),
            execution("f1", "fund-1", "Buy", 995, 0.05, exec_type="Funding", maker=None, at=20),
            execution("e2", "o2", "Sell", 1010, 0.202, at=30),
            execution("x9", "someone-else", "Buy", 1000, 9.9, at=10),  # 不是這次執行下的單
        ])
        rec = make_recorder(db, tmp_path, executions)
        rec.start_run(run_info())
        rec.record_order(order("o1", "entry", "Buy", "closed", avg=990.0, filled=1.0))
        rec.record_order(order("o2", "exit", "Sell", "closed", price=1010.0, avg=1010.0, filled=1.0, at=30))
        rec.record_event(event())

        [e] = rows(db, SlEvent)
        assert float(e.fees) == pytest.approx(0.198 + 0.202)
        assert float(e.funding) == pytest.approx(0.05)
        assert float(e.net_pnl) == pytest.approx(20.0 - 0.4 - 0.05)
        fills = {f.exec_id: f for f in rows(db, SlFill)}
        assert set(fills) == {"e1", "e2", "f1"}
        assert fills["e1"].event_index == 1 and fills["e1"].is_maker is True
        assert fills["f1"].exec_type == "Funding"

    def test_end_run_resyncs_fills_and_writes_summary(self, db, tmp_path):
        executions = FakeExecutions([])
        rec = make_recorder(db, tmp_path, executions)
        rec.start_run(run_info())
        rec.record_order(order("o1", "entry", "Buy", "closed", avg=990.0, filled=1.0))
        rec.record_order(order("o2", "exit", "Sell", "closed", price=1010.0, avg=1010.0, filled=1.0, at=30))
        rec.record_event(event())  # 這時候交易所還沒回報成交明細
        executions.items = [execution("e1", "o1", "Buy", 990, 0.198, at=1), execution("e2", "o2", "Sell", 1010, 0.202, at=30)]

        rec.end_run(t(60), "loop_done")

        [e] = rows(db, SlEvent)
        assert float(e.fees) == pytest.approx(0.4)  # 收尾時補同步後重算
        [run] = rows(db, SlRun)
        assert run.events_count == 1
        assert float(run.net_pnl) == pytest.approx(19.6)
        assert float(run.max_drawdown) == pytest.approx(-5.0)


class TestDatabaseDownDoesNotStopTrading:
    def test_falls_back_to_local_file_and_backfill_loads_it(self, db, tmp_path):
        rec = TradeRecorder(db_url="postgresql+psycopg2://u:p@127.0.0.1:1/none", pending_dir=tmp_path / "pending",
                            fetch_executions=FakeExecutions([]), connect_timeout=1)
        rec.start_run(run_info())  # 不能 raise
        rec.record_order(order("o1", "entry", "Buy", "open"))
        rec.end_run(t(60), "stop_requested")

        assert rec.db_available is False
        [pending] = list((tmp_path / "pending").glob("*.jsonl"))
        lines = [json.loads(l) for l in pending.read_text().splitlines()]
        assert {l["table"] for l in lines} >= {"sl_run", "sl_order"}

        backfill(db, tmp_path / "pending")
        assert len(rows(db, SlRun)) == 1 and len(rows(db, SlOrder)) == 1
        backfill(db, tmp_path / "pending")  # 再跑一次不會重複
        assert len(rows(db, SlRun)) == 1

    def test_without_db_url_everything_goes_to_the_local_file(self, tmp_path):
        rec = TradeRecorder(db_url=None, pending_dir=tmp_path / "pending", fetch_executions=FakeExecutions([]))
        rec.start_run(run_info())
        assert list((tmp_path / "pending").glob("*.jsonl"))


class TestSetupDb:
    def test_creates_the_four_tables_and_is_idempotent(self, tmp_path):
        from strategy_lab.storage.setup_db import setup

        url = f"sqlite:///{tmp_path / 'fresh.db'}"
        assert setup(url) == ["sl_event", "sl_fill", "sl_order", "sl_run"]
        assert setup(url) == ["sl_event", "sl_fill", "sl_order", "sl_run"]


class TestTableComments:
    """每張表、每個欄位都有中文說明,setup 時寫進 Postgres(COMMENT ON),
    在 Superset / 任何 DB 工具打開表就看得到,不用翻程式。"""

    def test_every_table_and_column_has_a_comment(self):
        missing = [t.name for t in Base.metadata.sorted_tables if not (t.comment or "").strip()]
        missing += [f"{t.name}.{c.name}" for t in Base.metadata.sorted_tables
                    for c in t.columns if not (c.comment or "").strip()]
        assert missing == []

    def test_comment_statements_cover_every_table_and_column(self):
        from strategy_lab.storage.setup_db import comment_statements

        stmts = comment_statements(Base.metadata)
        columns = sum(len(t.columns) for t in Base.metadata.sorted_tables)
        assert len(stmts) == len(Base.metadata.sorted_tables) + columns
        assert any(s.startswith("COMMENT ON TABLE sl_run IS '") for s in stmts)
        assert any(s.startswith("COMMENT ON COLUMN sl_fill.fee IS '") for s in stmts)

    def test_single_quote_in_a_comment_is_escaped(self):
        from sqlalchemy import Column, Integer, MetaData, Table

        from strategy_lab.storage.setup_db import comment_statements

        md = MetaData()
        Table("x", md, Column("a", Integer, primary_key=True, comment="it's"), comment="表")
        assert comment_statements(md) == ["COMMENT ON TABLE x IS '表'", "COMMENT ON COLUMN x.a IS 'it''s'"]

    def test_comments_are_sent_as_raw_sql_so_colons_are_not_bind_params(self):
        """說明裡有「(USDT):正數」這種冒號,用 sqlalchemy text() 會被當成 :正數 參數而失敗
        (2026-10-09 套用到真 DB 時發生),所以要用 exec_driver_sql 原樣送出。"""
        from strategy_lab.storage import setup_db

        sent = []

        class FakeConn:
            def exec_driver_sql(self, sql):
                sent.append(sql)

            def execute(self, *a, **k):
                raise AssertionError("不要用 text()/execute 送 COMMENT")

        class FakeBegin:
            def __enter__(self):
                return FakeConn()

            def __exit__(self, *exc):
                return False

        class FakeEngine:
            class dialect:
                name = "postgresql"

            def begin(self):
                return FakeBegin()

        setup_db._apply_comments(FakeEngine())
        assert sent == [s.replace("%", "%%") for s in setup_db.comment_statements(Base.metadata)]
        assert any("):正數" in s for s in sent)

    def test_percent_signs_are_doubled_for_psycopg2(self, monkeypatch):
        """psycopg2 的 exec_driver_sql 會把 % 當格式符號(crypto_database 2026-10-09 踩到),要寫成 %%"""
        from strategy_lab.storage import setup_db

        sent = []

        class FakeConn:
            def exec_driver_sql(self, sql):
                sent.append(sql)

        class FakeBegin:
            def __enter__(self):
                return FakeConn()

            def __exit__(self, *exc):
                return False

        class FakeEngine:
            class dialect:
                name = "postgresql"

            def begin(self):
                return FakeBegin()

        monkeypatch.setattr(setup_db, "comment_statements", lambda md: ["COMMENT ON COLUMN x.a IS '勝率(%)'"])
        setup_db._apply_comments(FakeEngine())
        assert sent == ["COMMENT ON COLUMN x.a IS '勝率(%%)'"]


class TestLotColumn:
    def test_order_lot_is_stored(self, db, tmp_path):
        rec = make_recorder(db, tmp_path)
        rec.start_run(run_info())
        record = order("o9", "entry", "Buy", "open")
        record.lot = 2
        rec.record_order(record)
        [o] = rows(db, SlOrder)
        assert o.lot == 2

    def test_exit_hold_seconds_is_stored(self, db, tmp_path):
        """平倉單記這一注的持倉秒數(建倉成交 → 平倉成交,2026-10-09)"""
        rec = make_recorder(db, tmp_path)
        rec.start_run(run_info())
        record = order("o9", "exit", "Sell", "closed", avg=1000.0, filled=1.0)
        record.lot, record.hold_seconds = 1, 11520
        rec.record_order(record)
        [o] = rows(db, SlOrder)
        assert o.hold_seconds == 11520


class TestSetupAddsNewColumns:
    def test_existing_sl_order_without_lot_gets_the_column(self, tmp_path):
        from sqlalchemy import create_engine, inspect, text

        from strategy_lab.storage.setup_db import setup

        url = f"sqlite:///{tmp_path / 'old.db'}"
        engine = create_engine(url, future=True)
        with engine.begin() as conn:  # 加 lot 欄位之前建好的舊表
            conn.execute(text("CREATE TABLE sl_order (order_id VARCHAR(64) PRIMARY KEY, purpose VARCHAR(20))"))

        setup(url)
        setup(url)  # 再跑一次不會出錯

        assert "lot" in {c["name"] for c in inspect(create_engine(url)).get_columns("sl_order")}



class TestRawDurations:
    """時長、預估時長以原始欄位存進資料庫(2026-10-11),不用在 Superset 算。"""

    def test_event_hold_and_expected_hold_seconds(self, db, tmp_path):
        rec = make_recorder(db, tmp_path)
        rec.start_run(run_info(expected_hold_seconds=4 * 3600))
        rec.record_event(event(start=0, end=90))
        [e] = rows(db, SlEvent)
        assert e.hold_seconds == 90 * 60
        assert e.expected_hold_seconds == 4 * 3600

    def test_event_without_expected_hold_is_null(self, db, tmp_path):
        rec = make_recorder(db, tmp_path)
        rec.start_run(run_info())
        rec.record_event(event(start=0, end=30))
        [e] = rows(db, SlEvent)
        assert e.hold_seconds == 1800 and e.expected_hold_seconds is None

    def test_run_expected_end_and_actual_duration(self, db, tmp_path):
        rec = make_recorder(db, tmp_path)
        rec.start_run(run_info(expected_end_at=t(600), expected_duration_seconds=600 * 60))
        [run] = rows(db, SlRun)
        assert run.duration_seconds is None  # 還在跑
        assert run.expected_duration_seconds == 36000
        rec.end_run(t(125), "stop_requested")
        [run] = rows(db, SlRun)
        assert run.duration_seconds == 125 * 60
        assert run.expected_end_at.replace(tzinfo=timezone.utc) == t(600)  # SQLite 不存時區

    def test_new_columns_survive_the_local_file_and_backfill(self, db, tmp_path):
        rec = make_recorder(None, tmp_path)
        rec.start_run(run_info(expected_end_at=t(600), expected_duration_seconds=36000, expected_hold_seconds=60))
        rec.record_event(event(start=0, end=30))
        rec.end_run(t(60), "loop_done")
        backfill(db, tmp_path / "pending")
        [run], [e] = rows(db, SlRun), rows(db, SlEvent)
        assert (run.duration_seconds, run.expected_duration_seconds, run.expected_hold_seconds) == (3600, 36000, 60)
        assert (e.hold_seconds, e.expected_hold_seconds) == (1800, 60)

    def test_setup_adds_the_new_columns_to_existing_tables(self, tmp_path):
        from sqlalchemy import inspect, text

        from strategy_lab.storage.setup_db import setup

        url = f"sqlite:///{tmp_path / 'old.db'}"
        engine = create_engine(url, future=True)
        with engine.begin() as conn:  # 加欄位之前的舊表(只留主鍵)
            conn.execute(text("CREATE TABLE sl_run (run_id VARCHAR(36) PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE sl_event (run_id VARCHAR(36), event_index INTEGER, PRIMARY KEY (run_id, event_index))"))
        setup(url)
        inspector = inspect(create_engine(url))
        run_cols = {c["name"] for c in inspector.get_columns("sl_run")}
        event_cols = {c["name"] for c in inspector.get_columns("sl_event")}
        assert {"duration_seconds", "expected_end_at", "expected_duration_seconds", "expected_hold_seconds"} <= run_cols
        assert {"hold_seconds", "expected_hold_seconds"} <= event_cols


class TestCostsForNotifications:
    """Telegram 的 loop 結算要顯示淨利:手續費/資金費用 Bybit 成交明細的真實數字。"""

    def test_event_costs_and_none_before_exchange_reports(self, db, tmp_path):
        executions = FakeExecutions([])
        rec = make_recorder(db, tmp_path, executions)
        rec.start_run(run_info())
        rec.record_order(order("o1", "entry", "Buy", "closed", avg=990.0, filled=1.0))
        rec.record_order(order("o2", "exit", "Sell", "closed", price=1010.0, avg=1010.0, filled=1.0, at=30))
        rec.record_event(event())
        assert rec.event_costs(1) is None  # 交易所還沒回報 → 呼叫端顯示「待查」

        executions.items = [
            execution("e1", "o1", "Buy", 990, 0.198, at=1),
            execution("f1", "fund-1", "Buy", 995, 0.05, exec_type="Funding", maker=None, at=20),
            execution("e2", "o2", "Sell", 1010, 0.202, at=30),
        ]
        rec.record_event(event())
        fees, funding = rec.event_costs(1)
        assert fees == pytest.approx(0.4) and funding == pytest.approx(0.05)
