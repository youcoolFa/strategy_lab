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


def add_run(db, tmp_path, name, symbol, started, git="abc1234", ended=None):
    rec = TradeRecorder(db_url=db, pending_dir=tmp_path / "p", fetch_executions=lambda *a: [])
    run_id = rec.start_run(RunInfo(
        strategy_name=name, strategy_path="NA", strategy_yaml="NA", strategy_params={}, config={},
        symbol=symbol, category="linear", direction="long", origin_price=None, origin_source=None, qty=None,
        order_type="limit", loop=None, testnet=False, preflight=None, log_path=None, started_at=started))
    rec._run["git_commit"] = git
    rec._write("sl_run", rec._run)
    if ended is not None:
        rec.end_run(ended, "stop_requested")
    return run_id


class TestRuns:
    def test_recent_runs_newest_first_and_unfinished_by_symbol(self, db, tmp_path):
        add_run(db, tmp_path, "scale_in_ladder", "SUIUSDT", NOW - timedelta(days=3), ended=NOW - timedelta(days=2))
        add_run(db, tmp_path, "scale_in_ladder", "SUIUSDT", NOW - timedelta(days=1), git="f251f84")
        add_run(db, tmp_path, "free style", "ETHUSDT", NOW - timedelta(hours=1))

        runs = data.recent_runs(db, limit=10)
        assert [r["strategy_name"] for r in runs] == ["free style", "scale_in_ladder", "scale_in_ladder"]
        assert runs[0]["started_at"].tzinfo is not None

        unfinished = data.unfinished_by_symbol(db)
        assert set(unfinished) == {"SUIUSDT", "ETHUSDT"}
        assert unfinished["SUIUSDT"]["git_commit"] == "f251f84"

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
