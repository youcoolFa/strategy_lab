"""Streamlit 介面(第一階段:唯讀狀態頁 + free style)背後的資料層 strategy_lab/ui/data.py。
畫面只負責顯示;這裡的函式不碰 streamlit,可以單獨測(2026-10-09)。"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from strategy_lab.storage.recorder import RunInfo, TradeRecorder
from strategy_lab.storage.setup_db import setup
from strategy_lab.ui import data

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "run").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "live_execution_config.yaml").write_text("symbol_override: SUIUSDT\ndry_run: false\n")
    (root / "live_wld_long.yaml").write_text("symbol_override: WLDUSDT\n")
    (root / "live_execution_config.example.yaml").write_text("symbol_override: BTCUSDT\n")
    return root


class TestDaemons:
    def test_lists_each_live_config_with_pid_alive_and_symbol(self, project, monkeypatch):
        (project / "run" / "live_execution_config.pid").write_text("54931")
        monkeypatch.setattr(data, "is_alive", lambda pid: pid == 54931)

        rows = {r.config: r for r in data.list_daemons(project)}

        assert set(rows) == {"live_execution_config", "live_wld_long"}  # 範本不算
        sui = rows["live_execution_config"]
        assert (sui.symbol, sui.pid, sui.alive) == ("SUIUSDT", 54931, True)
        wld = rows["live_wld_long"]
        assert (wld.symbol, wld.pid, wld.alive) == ("WLDUSDT", None, False)

    def test_last_log_time_is_the_newest_of_its_log_files(self, project):
        console = project / "logs" / "live_execution_config.console.log"
        run_log = project / "logs" / "live_execution_config_20261007_041641.log"
        console.write_text("x")
        run_log.write_text("y")
        old, new = NOW - timedelta(hours=3), NOW - timedelta(minutes=10)
        os.utime(console, (old.timestamp(), old.timestamp()))
        os.utime(run_log, (new.timestamp(), new.timestamp()))

        [row] = [r for r in data.list_daemons(project) if r.config == "live_execution_config"]
        assert row.last_log_at == new.replace(microsecond=0) or abs((row.last_log_at - new).total_seconds()) < 1
        assert row.console_log == console


class TestStaleness:
    def test_running_daemon_silent_for_more_than_70_minutes_is_flagged(self):
        """每小時一定有 ⚪ 狀態回報;超過 70 分鐘沒寫 log = Mac 睡著或卡住"""
        assert data.stale_warning(True, NOW - timedelta(minutes=71), NOW) is not None
        assert data.stale_warning(True, NOW - timedelta(minutes=30), NOW) is None
        assert data.stale_warning(False, NOW - timedelta(days=3), NOW) is None  # 沒在跑不用提醒
        assert data.stale_warning(True, None, NOW) is not None


class TestCodeVersion:
    def test_marks_old_code(self):
        assert data.code_version_label("f251f84399d687af", "9cf3920") == "f251f84 ⚠️ 舊碼(HEAD 9cf3920)"
        assert data.code_version_label("9cf3920aaaa", "9cf3920") == "9cf3920(最新)"
        assert data.code_version_label("50817251-dirty", "9cf3920") == "5081725-dirty ⚠️ 舊碼(HEAD 9cf3920)"
        assert data.code_version_label(None, "9cf3920") == "—"


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'trading.db'}"
    setup(url)
    return url


def add_run(db, tmp_path, name, symbol, started, git="abc1234", ended=None, account=None):
    rec = TradeRecorder(db_url=db, pending_dir=tmp_path / "p", fetch_executions=lambda *a: [])
    run_id = rec.start_run(RunInfo(
        strategy_name=name, strategy_path="NA", strategy_yaml="NA", strategy_params={}, config={},
        symbol=symbol, category="linear", direction="long", origin_price=None, origin_source=None, qty=None,
        order_type="limit", loop=None, testnet=False, preflight=None, log_path=None, started_at=started,
        account=account))
    rec._run["git_commit"] = git
    rec._write("sl_run", rec._run)
    if ended is not None:
        rec.end_run(ended, "stop_requested")
    return run_id


class TestRuns:
    def test_recent_runs_newest_first_and_unfinished_runs(self, db, tmp_path):
        add_run(db, tmp_path, "scale_in_ladder", "SUIUSDT", NOW - timedelta(days=3), ended=NOW - timedelta(days=2))
        add_run(db, tmp_path, "scale_in_ladder", "SUIUSDT", NOW - timedelta(days=1), git="f251f84")
        add_run(db, tmp_path, "free style", "ETHUSDT", NOW - timedelta(hours=1))

        runs = data.recent_runs(db, limit=10)
        assert [r["strategy_name"] for r in runs] == ["free style", "scale_in_ladder", "scale_in_ladder"]
        assert runs[0]["started_at"].tzinfo is not None

        unfinished = data.unfinished_runs(db)
        assert set(unfinished) == {("TEST1", "SUIUSDT"), ("TEST1", "ETHUSDT")}
        assert unfinished[("TEST1", "SUIUSDT")]["git_commit"] == "f251f84"

    def test_free_style_running(self, db, tmp_path):
        add_run(db, tmp_path, "free style", "ETHUSDT", NOW - timedelta(hours=1))
        add_run(db, tmp_path, "scale_in_ladder", "SUIUSDT", NOW - timedelta(days=1))
        assert [r["symbol"] for r in data.free_style_running(db)] == ["ETHUSDT"]


class TestAccount:
    class FakeClient:
        def get_account_equity(self):
            return 53.6

        def list_positions(self):
            return [{"symbol": "SUIUSDT", "side": "Buy", "size": "60", "avgPrice": "1.22541667",
                     "markPrice": "1.1255", "unrealisedPnl": "-5.98", "leverage": "10"}]

        def list_open_orders(self):
            return [{"symbol": "SUIUSDT", "side": "Sell", "orderType": "Limit", "price": "1.2322", "qty": "20",
                     "reduceOnly": True, "orderId": "a1b2c3d4-xxxx", "createdTime": "1791331200000"}]

    def test_snapshot_shapes_positions_and_orders_for_display(self):
        snap = data.account_snapshot(self.FakeClient())
        assert snap["equity"] == 53.6
        assert snap["positions"] == [{"幣種": "SUIUSDT", "方向": "多", "數量": 60.0, "均價": 1.22541667,
                                      "標記價": 1.1255, "未實現": -5.98, "槓桿": "10"}]
        [o] = snap["orders"]
        assert (o["幣種"], o["方向"], o["價格"], o["數量"], o["只減倉"], o["orderId"]) == \
            ("SUIUSDT", "Sell", 1.2322, 20.0, True, "a1b2c3d4")


class TestLogTail:
    def test_last_n_lines(self, tmp_path):
        p = tmp_path / "a.log"
        p.write_text("\n".join(f"line {i}" for i in range(100)))
        assert data.log_tail(p, 3) == "line 97\nline 98\nline 99"
        assert data.log_tail(tmp_path / "missing.log", 3) == ""


class TestActiveRuns:
    """「策略」分頁的進行中清單:在跑的策略 daemon + 進行中的 free style,不分種類列在一起(2026-10-09)。"""

    def test_combines_running_daemons_and_free_style(self, project, db, tmp_path, monkeypatch):
        (project / "live_execution_config.yaml").write_text(
            "strategy_path: strategies/scale_in_ladder.yaml\nsymbol_override: SUIUSDT\ndry_run: false\ntestnet: false\n")
        (project / "run" / "live_execution_config.pid").write_text("54931")
        monkeypatch.setattr(data, "is_alive", lambda pid: pid == 54931)
        add_run(db, tmp_path, "scale_in_ladder", "SUIUSDT", NOW - timedelta(days=1), git="f251f84")
        add_run(db, tmp_path, "free style", "ETHUSDT", NOW - timedelta(hours=1))

        rows = data.active_runs(db, project)

        assert [(r["kind"], r["symbol"], r["strategy"]) for r in rows] == [
            ("strategy", "SUIUSDT", "scale_in_ladder"), ("free style", "ETHUSDT", "free style")]
        sui, eth = rows
        assert sui["config"] == "live_execution_config" and sui["mode"] == "🔴 實盤"
        assert sui["started_at"] == NOW - timedelta(days=1)
        assert eth["config"] is None and eth["mode"] == "⏺ 記錄中"

    def test_stopped_daemons_are_not_listed(self, project, db):
        assert data.active_runs(db, project) == []

    def test_without_db_still_lists_daemons(self, project, monkeypatch):
        (project / "run" / "live_wld_long.pid").write_text("777")
        monkeypatch.setattr(data, "is_alive", lambda pid: True)
        [row] = data.active_runs(None, project)
        assert row["symbol"] == "WLDUSDT" and row["started_at"] is None
        assert row["mode"] == "⚪ DRY RUN"  # 設定檔沒寫 = 安全預設


class TestProjectRoot:
    def test_config_path_follows_project_root(self, monkeypatch, tmp_path):
        """畫面用 project_root() 找設定檔;測試把它換成臨時資料夾就不會碰到真的 live_*.yaml"""
        monkeypatch.setattr(data, "project_root", lambda: tmp_path)
        assert data.config_path("live_x") == tmp_path / "live_x.yaml"

    def test_default_is_the_real_project(self):
        assert data.project_root() == data.PROJECT_ROOT


class TestSchedule:
    """總覽的「啟動時間 / 預估結束 / 預估長度」(2026-10-10):預估結束 = 策略時間窗的強制收尾時間。"""

    def test_scale_in_ladder_started_on_wednesday_ends_monday_0555(self, tmp_path):
        from zoneinfo import ZoneInfo

        hkt = ZoneInfo("Asia/Hong_Kong")
        cfg = tmp_path / "live_x.yaml"
        cfg.write_text("strategy_path: strategies/scale_in_ladder.yaml\n")
        start = datetime(2026, 10, 7, 4, 16, 41, tzinfo=hkt)
        s = data.schedule(cfg, start)
        assert s["start"] == start
        assert s["end"] == datetime(2026, 10, 12, 5, 55, tzinfo=hkt)
        # 畫面只到分鐘:長度用截到分鐘的啟動時間算,畫面上 04:16 → 05:55 的心算結果才對得上
        assert s["duration"] == timedelta(days=5, hours=1, minutes=39)

    def test_start_time_from_the_db_is_utc_and_must_be_read_as_hkt(self, tmp_path):
        """資料庫存的是 UTC(10-06 20:16 UTC = 10-07 04:16 HKT);時間窗是照 HKT 算的,
        直接拿 UTC 去算會差 8 小時(2026-10-10 真實資料踩到:顯示 10-12 13:55)。"""
        from zoneinfo import ZoneInfo

        cfg = tmp_path / "live_x.yaml"
        cfg.write_text("strategy_path: strategies/scale_in_ladder.yaml\n")
        start_utc = datetime(2026, 10, 6, 20, 16, 41, tzinfo=timezone.utc)
        s = data.schedule(cfg, start_utc)
        assert s["end"] == datetime(2026, 10, 12, 5, 55, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        assert data.fmt_time(s["end"]) == "10-12 05:55"
        assert data.fmt_duration(s["duration"]) == "5 天 1 小時 39 分"

    def test_unknown_start_or_broken_config_gives_none(self, tmp_path):
        cfg = tmp_path / "live_x.yaml"
        cfg.write_text("strategy_path: strategies/does_not_exist.yaml\n")
        assert data.schedule(cfg, None) is None
        assert data.schedule(cfg, NOW) is None


class TestMinuteFormatting:
    def test_times_and_durations_show_minutes_only(self):
        from zoneinfo import ZoneInfo

        t = datetime(2026, 10, 12, 5, 55, 37, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        assert data.fmt_time(t) == "10-12 05:55"
        assert data.fmt_time(None) == "—"
        assert data.fmt_duration(timedelta(days=4, hours=1, minutes=38, seconds=59)) == "4 天 1 小時 38 分"
        assert data.fmt_duration(None) == "—"


class TestMultiAccount:
    """多帳戶(2026-10-12):同一個幣可以在兩個帳戶各跑一段;畫面要分得出是哪個帳戶。"""

    def test_daemon_rows_carry_the_account(self, project):
        (project / "live_main_eth.yaml").write_text("symbol_override: ETHUSDT\naccount: MAIN\n")
        rows = {r.config: r.account for r in data.list_daemons(project)}
        assert rows == {"live_execution_config": "test1", "live_main_eth": "MAIN", "live_wld_long": "test1"}

    def test_unfinished_runs_keyed_by_account_and_symbol(self, db, tmp_path):
        add_run(db, tmp_path, "scale_in_ladder", "SUIUSDT", NOW - timedelta(days=1))  # 舊紀錄沒帳戶 = test1
        add_run(db, tmp_path, "free style", "SUIUSDT", NOW - timedelta(hours=1), account="MAIN")
        runs = data.unfinished_runs(db)
        assert set(runs) == {("TEST1", "SUIUSDT"), ("MAIN", "SUIUSDT")}
        assert [(r["symbol"], r["account"]) for r in data.free_style_running(db)] == [("SUIUSDT", "MAIN")]

    def test_active_runs_match_daemon_by_account(self, project, db, tmp_path, monkeypatch):
        (project / "live_main_sui.yaml").write_text(
            "strategy_path: strategies/weekend_band.yaml\nsymbol_override: SUIUSDT\naccount: main\ndry_run: false\n")
        (project / "run" / "live_main_sui.pid").write_text("888")
        monkeypatch.setattr(data, "is_alive", lambda pid: pid == 888)
        add_run(db, tmp_path, "scale_in_ladder", "SUIUSDT", NOW - timedelta(days=1))  # test1 的,不是這個 daemon
        add_run(db, tmp_path, "weekend_band", "SUIUSDT", NOW - timedelta(hours=2), account="MAIN")
        add_run(db, tmp_path, "free style", "ETHUSDT", NOW - timedelta(hours=1))

        rows = data.active_runs(db, project)

        assert [(r["account"], r["symbol"], r["strategy"], r["started_at"]) for r in rows] == [
            ("main", "SUIUSDT", "weekend_band", NOW - timedelta(hours=2)),
            ("test1", "ETHUSDT", "free style", NOW - timedelta(hours=1))]

    def test_list_accounts_always_has_the_default(self, tmp_path, monkeypatch):
        from strategy_lab.live import accounts

        env = tmp_path / ".env"
        env.write_text("ACCOUNT = MAIN\nBYBIT_MAIN_API_KEY=k\nBYBIT_MAIN_API_SECRET=s\n")
        monkeypatch.setattr(accounts, "ENV_PATH", env)
        assert data.list_accounts() == ["MAIN", "test1"]  # main 排第一 = 頁面預設選它(2026-10-12)

    def test_list_accounts_without_main_puts_test1_first(self, tmp_path, monkeypatch):
        from strategy_lab.live import accounts

        env = tmp_path / ".env"
        env.write_text("ACCOUNT = sub2\nBYBIT_sub2_API_KEY=k\nBYBIT_sub2_API_SECRET=s\n")
        monkeypatch.setattr(accounts, "ENV_PATH", env)
        assert data.list_accounts() == ["test1", "sub2"]

    def test_make_client_uses_that_accounts_keys(self, tmp_path, monkeypatch):
        from strategy_lab.live import accounts

        env = tmp_path / ".env"
        env.write_text("BYBIT_API_KEY=legacy\nBYBIT_API_SECRET=x\nACCOUNT = MAIN\nBYBIT_MAIN_API_KEY=mainkey\nBYBIT_MAIN_API_SECRET=s\n")
        monkeypatch.setattr(accounts, "ENV_PATH", env)
        assert data.make_client("main")._http.api_key == "mainkey"
        assert data.make_client()._http.api_key == "legacy"


class TestPnlTable:
    """損益表(2026-10-14):選帳戶 + 策略,每一對交易(sl_event)一列,最近的在前。"""

    def add_round(self, db, tmp_path, account, strategy, symbol, start, minutes, entry, exit_, qty=2.0, fees=0.5,
                  direction="long", **run_extra):
        from strategy_lab.engine.events import Event

        rec = TradeRecorder(db_url=db, pending_dir=tmp_path / "p", fetch_executions=lambda *a: [])
        rec.start_run(RunInfo(
            strategy_name=strategy, strategy_path="NA", strategy_yaml="NA", strategy_params={}, config={},
            symbol=symbol, category="linear", direction=direction, origin_price=None, origin_source=None, qty=None,
            order_type="NA", loop=None, testnet=False, preflight=None, log_path=None, started_at=start,
            account=account, **run_extra))
        sign = 1 if direction == "long" else -1
        ev = Event(index=1, direction=direction, start_time=start, end_time=start + timedelta(minutes=minutes), fills=2,
                   max_position=qty, avg_entry=entry, avg_exit=exit_, realized_pnl=sign * (exit_ - entry) * qty,
                   max_drawdown=-1.0, forced=False)
        rec._fills["x"] = {"exec_id": "x", "event_index": 1, "exec_type": "Trade", "fee": fees}
        rec.record_event(ev)

    def test_rows_for_account_and_strategy_newest_first(self, db, tmp_path):
        self.add_round(db, tmp_path, "MAIN", "free style", "ETHUSDT", NOW - timedelta(days=2), 90, 2500, 2510,
                       expected_hold_seconds=4 * 3600, target_price=2520.0, stop_price=2480.0, note="試單")
        self.add_round(db, tmp_path, "MAIN", "free style", "ETHUSDT", NOW - timedelta(days=1), 30, 2600, 2590)
        self.add_round(db, tmp_path, "MAIN", "scale_in_ladder", "SUIUSDT", NOW, 10, 1.2, 1.3)  # 別的策略
        self.add_round(db, tmp_path, "test1", "free style", "ETHUSDT", NOW, 10, 1, 2)  # 別的帳戶

        rows = data.pnl_rows(db, "main", "free style")

        assert [r["建倉價格"] for r in rows] == [2600.0, 2500.0]  # 最近的在前
        older = rows[1]
        assert older["日期"] == "2026/10/07" and older["建倉時間"] == "10-07 18:00"
        assert (older["幣種"], older["方向"], older["數量"], older["平倉價格"]) == ("ETHUSDT", "long", 2.0, 2510.0)
        assert older["真實損益"] == pytest.approx(20.0 - 0.5) and older["手續費"] == pytest.approx(0.5)
        assert older["手續費佔比"] == pytest.approx(2.5)  # 0.5 / 20 毛利
        assert (older["預估時長"], older["預估單位"], older["實際時長"]) == (4, "小時", "1 小時 30 分")
        assert (older["預估秒數"], older["實際秒數"]) == (4 * 3600, 5400)  # 給總結列算平均,畫面不顯示
        assert (older["目標價"], older["停損價"], older["備註"]) == (2520.0, 2480.0, "試單")
        assert rows[0]["預估時長"] is None and rows[0]["預估單位"] == "" and rows[0]["目標價"] is None
        assert {"run_id", "event_index"} <= set(older)  # 存檔用,畫面不顯示

    def test_strategies_of_an_account_start_with_free_style(self, db, tmp_path):
        self.add_round(db, tmp_path, "MAIN", "scale_in_ladder", "SUIUSDT", NOW, 10, 1.2, 1.3)
        assert data.pnl_strategies(db, "MAIN") == ["free style", "scale_in_ladder"]
        assert data.pnl_strategies(db, "nobody") == ["free style"]

    def test_old_rows_without_account_belong_to_test1(self, db, tmp_path):
        self.add_round(db, tmp_path, None, "scale_in_ladder", "SUIUSDT", NOW, 10, 1.2, 1.3)
        assert len(data.pnl_rows(db, "test1", "scale_in_ladder")) == 1

    @pytest.mark.parametrize("seconds,parts", [(None, (None, "")), (45, (45, "秒")), (5400, (90, "分鐘")),
                                               (4 * 3600, (4, "小時")), (36 * 3600, (1.5, "天")), (2 * 86400, (2, "天"))])
    def test_hold_parts_number_and_unit(self, seconds, parts):
        """預估時長拆成「數字 + 單位」(2026-10-14 使用者:單位用下拉選,不用手打 4h)"""
        assert data.hold_parts(seconds) == parts
        if seconds:
            assert data.hold_seconds(*parts) == seconds


def test_pnl_summary_row():
    """損益表最底的總結列(2026-10-14):數量平均、真實損益總和、手續費總和、手續費佔比平均、
    實際時長平均、預估時長平均(只算有填的);其他欄位空白。"""
    rows = [{"數量": 10.0, "真實損益": 60.24, "手續費": 19.6, "手續費佔比": 24.4, "實際秒數": 3600, "預估秒數": 4 * 3600},
            {"數量": 5.0, "真實損益": -20.0, "手續費": 3.7, "手續費佔比": None, "實際秒數": 7200, "預估秒數": None},
            {"數量": 3.0, "真實損益": 1.0, "手續費": 0.3, "手續費佔比": 10.0, "實際秒數": 1800, "預估秒數": 2 * 3600}]
    s = data.pnl_summary(rows)
    assert s["日期"] == "總結(3 對)"
    assert s["數量"] == pytest.approx(6.0) and s["真實損益"] == pytest.approx(41.24) and s["手續費"] == pytest.approx(23.6)
    assert s["手續費佔比"] == pytest.approx(17.2)  # 只平均有數字的
    assert s["實際時長"] == "1 小時 10 分" and s["預估時長"] == "3 小時 0 分"
    assert {k for k, v in s.items() if v not in ("", None)} == {"日期", "勝率", "數量", "真實損益", "手續費", "手續費佔比", "實際時長", "預估時長"}


def test_pnl_summary_of_nothing_is_empty():
    from strategy_lab.ui.data import pnl_summary

    assert pnl_summary([]) == {}


def test_fee_points_round_trip_at_maker_rate():
    """損益表的手續費點數(2026-10-14 使用者):ETHUSDT 現價 × maker 費率 × 2(買 + 賣)
    = 價格要往有利方向走幾點才賺回手續費。費率是這個帳戶的真實費率。"""
    class Client:
        def get_last_price(self, symbol):
            assert symbol == "ETHUSDT"
            return 2516.0

        def get_fee_rates(self, symbol):
            return 0.0002, 0.00055

    got = data.fee_points(Client())
    assert (got["symbol"], got["price"], got["maker_rate"]) == ("ETHUSDT", 2516.0, 0.0002)
    assert got["points"] == pytest.approx(2516.0 * 0.0002 * 2)


def test_pnl_summary_win_rate():
    """勝率(2026-10-21 使用者):真實損益(淨利)> 0 的對數 ÷ 總對數;剛好 0 不算贏"""
    rows = [{"數量": 1.0, "真實損益": 5.0, "手續費": 1.0, "手續費佔比": None, "實際秒數": 60, "預估秒數": None},
            {"數量": 1.0, "真實損益": -3.0, "手續費": 1.0, "手續費佔比": None, "實際秒數": 60, "預估秒數": None},
            {"數量": 1.0, "真實損益": 0.0, "手續費": 1.0, "手續費佔比": None, "實際秒數": 60, "預估秒數": None},
            {"數量": 1.0, "真實損益": 8.0, "手續費": 1.0, "手續費佔比": None, "實際秒數": 60, "預估秒數": None}]
    assert data.pnl_summary(rows)["勝率"] == pytest.approx(0.5)
