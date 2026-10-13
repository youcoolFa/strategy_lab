"""Streamlit 畫面(strategy_lab/ui/app.py)的測試:用 streamlit 的 AppTest 真的跑整頁,
資料層 / 動作層換成假的(不碰網路、資料庫、daemon)。

分頁:總覽 / 交易紀錄 / Log / 策略。「策略」分頁(2026-10-09 改版)= 選策略(free style 是其中一個)→
填參數 → 開始;上面是進行中清單(在跑的策略 + 進行中的 free style),每一列有自己的改參數 / 停止 / 脫離 / 結束。
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from strategy_lab.live import free_style as fs  # noqa: E402
from strategy_lab.ui import actions, data  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
APP = str(ROOT / "strategy_lab" / "ui" / "app.py")
NOW = datetime.now(timezone.utc)

SUI_CONFIG = """strategy_path: strategies/scale_in_ladder.yaml
symbol_override: SUIUSDT
entry_prices: [1.2297, 1.2291, 1.2285]
dry_run: false
testnet: false
position_sizing:
  mode: fixed_qty
  value: 20
adopt_existing_position: false
"""


class FakeClient:
    def get_account_equity(self):
        return 53.6

    def list_positions(self):
        return [{"symbol": "SUIUSDT", "side": "Buy", "size": "60", "avgPrice": "1.2254", "markPrice": "1.1255",
                 "unrealisedPnl": "-5.98", "leverage": "10"}]

    def list_open_orders(self):
        return []


UNRECORDED = []

SUI_ROW = {"kind": "strategy", "config": "live_execution_config", "account": "test1", "symbol": "SUIUSDT", "strategy": "scale_in_ladder",
           "mode": "🔴 實盤", "started_at": NOW}
ETH_ROW = {"kind": "free style", "config": None, "account": "test1", "symbol": "ETHUSDT", "strategy": "free style",
           "mode": "⏺ 記錄中", "started_at": NOW}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """SUI 的 scale_in_ladder 在跑(實盤、舊碼、90 分鐘沒寫 log);動作全部記進 calls,不真的做。"""
    import streamlit as st

    st.cache_data.clear()  # 頁面的 30 秒快取在同一個 process 裡共用,不清會讀到上一個測試的假帳戶
    calls = []
    root = tmp_path / "proj"
    root.mkdir()
    (root / "live_execution_config.yaml").write_text(SUI_CONFIG, encoding="utf-8")
    state = {"active": [SUI_ROW]}

    monkeypatch.setattr(data, "load_env", lambda: None)
    monkeypatch.setattr(data, "db_url", lambda: "sqlite://")
    monkeypatch.setattr(data, "make_client", lambda account=None: calls.append(("client", account)) or FakeClient())
    monkeypatch.setattr(data, "list_accounts", lambda: ["test1", "MAIN"])
    monkeypatch.setattr(data, "git_head", lambda *a: "9cf3920")
    monkeypatch.setattr(data, "project_root", lambda: root)
    monkeypatch.setattr(data, "list_daemons", lambda *a: [data.DaemonRow(
        config="live_execution_config", symbol="SUIUSDT", pid=54931, alive=True,
        console_log=tmp_path / "c.log", last_log_at=NOW - timedelta(minutes=90))])
    monkeypatch.setattr(data, "unfinished_runs", lambda url: {("TEST1", "SUIUSDT"): {
        "symbol": "SUIUSDT", "account": "test1", "strategy_name": "scale_in_ladder", "git_commit": "f251f84399",
        "started_at": NOW}})
    monkeypatch.setattr(data, "recent_runs", lambda url, limit=20: [])
    monkeypatch.setattr(data, "recent_events", lambda url, limit=20: [])
    monkeypatch.setattr(data, "active_runs", lambda url, root=None: list(state["active"]))

    monkeypatch.setattr(fs, "start", lambda *a, **k: calls.append(("fs_start", a[2])) or state.__setitem__("fs_when", a[3]) or
                        calls.append(("fs_account", k.get("account"))) or "run-1")
    monkeypatch.setattr(fs, "stop", lambda *a, **k: calls.append(("fs_stop", a[2])) or
                        calls.append(("fs_account", k.get("account"))) or "RESULT")
    monkeypatch.setattr(fs, "summary_message", lambda r: f"🏁 free style 結束|{r}")
    monkeypatch.setattr(fs, "send_telegram", lambda text: calls.append(("telegram", text)))

    from strategy_lab.live import reconcile as rc

    monkeypatch.setattr(rc, "load_unrecorded", lambda *a: list(UNRECORDED))
    monkeypatch.setattr(rc, "last_run", lambda *a: None)
    monkeypatch.setattr(rc, "run_daily", lambda db, now, **k: calls.append(("reconcile",)) or [])
    monkeypatch.setattr(actions, "daemon_running", lambda path: Path(path).name == "live_execution_config.yaml")
    monkeypatch.setattr(actions, "prepare_config", lambda root_, strategy, symbol, settings, params, **k: calls.append(
        ("prepare", strategy, symbol, settings, params)) or calls.append(("prepare_account", k.get("account"))) or (root / f"live_{Path(strategy).stem}_{symbol.lower()}.yaml"))
    monkeypatch.setattr(actions, "preflight_preview", lambda path: calls.append(("preview", Path(path).name)) or
                        actions.PreviewResult(text="【掛單計畫】(假的)", ready=True, fingerprint="fp", at=time.time()))
    monkeypatch.setattr(actions, "preflight_start", lambda path, preview, typed: calls.append(
        ("start", Path(path).name, typed)) or (typed in ("y", "yes"), "已在背景啟動(PID 1)" if typed in ("y", "yes") else "已取消"))
    monkeypatch.setattr(actions, "control_preview", lambda path, changes: calls.append(("ctl_preview", changes)) or "【變更預覽】")
    monkeypatch.setattr(actions, "control_apply", lambda path, changes, typed: calls.append(
        ("ctl_apply", changes, typed)) or (True, "✓ 已套用"))
    monkeypatch.setattr(actions.daemon, "stop", lambda path: calls.append(("stop", Path(path).name)) or "已停止")
    monkeypatch.setattr(actions.daemon, "detach", lambda path: calls.append(("detach", Path(path).name)) or "已脫離")
    return calls, state


def run():
    return AppTest.from_file(APP, default_timeout=30).run()


def buttons(at):
    return [b.key for b in at.button]


# ---------------------------------------------------------------- 總覽 / 重新整理 / 分頁

def test_overview_shows_daemon_old_code_stale_log_and_account(fake):
    at = run()
    assert not at.exception
    tables = " ".join(df.value.to_csv() for df in at.dataframe)  # 完整內容(str() 會被 pandas 截斷)
    assert "SUIUSDT" in tables and "舊碼" in tables and "分鐘沒寫 log" in tables
    assert any("53.6" in str(m.value) for m in at.metric)


def test_refresh_queries_bybit_again(fake, monkeypatch):
    made = []
    monkeypatch.setattr(data, "make_client", lambda account=None: made.append(1) or FakeClient())
    at = run()
    before = len(made)
    at.button(key="refresh").click().run()
    assert len(made) > before and not at.exception


def test_tabs(fake):
    at = run()
    assert [t.label for t in at.tabs] == ["總覽", "交易紀錄", "Log", "策略", "補記"]


# ---------------------------------------------------------------- 策略:選策略

def test_strategy_choices_are_every_strategy_yaml_plus_free_style(fake):
    at = run()
    options = list(at.radio(key="new_strategy").options)
    stems = sorted(p.stem for p in (ROOT / "strategies").glob("*.yaml"))
    assert options == stems + ["free style"]


# ---------------------------------------------------------------- 策略:free style

def test_free_style_start_requires_confirmation(fake):
    calls, _ = fake
    at = run()
    at.radio(key="new_strategy").set_value("free style").run()
    at.text_input(key="fs_symbol").input("ethusdt")
    at.button(key="fs_start").click().run()
    assert ("fs_start", "ETHUSDT") not in calls  # 沒勾確認
    at.checkbox(key="fs_start_confirm").check()
    at.button(key="fs_start").click().run()
    assert ("fs_start", "ETHUSDT") in calls and not at.exception




def test_free_style_symbol_only_needs_the_coin(fake):
    calls, _ = fake
    at = run()
    at.radio(key="new_strategy").set_value("free style").run()
    at.text_input(key="fs_symbol").input("eth").run()
    assert any("ETHUSDT" in c.value for c in at.caption)
    at.checkbox(key="fs_start_confirm").check()
    at.button(key="fs_start").click().run()
    assert ("fs_start", "ETHUSDT") in calls and not at.exception


def test_free_style_since_is_picked_and_defaults_to_now(fake):
    """補記開始時間用日期 + 時間選擇器,不用打字;預設 = 現在(日曆停在當年當月),可以往前選。"""
    calls, state = fake
    at = run()
    at.radio(key="new_strategy").set_value("free style").run()
    assert "fs_since" not in [t.key for t in at.text_input]
    now = datetime.now(timezone(timedelta(hours=8)))
    assert at.date_input(key="fs_since_day").value == now.date()
    picked = at.time_input(key="fs_since_time").value
    assert abs((picked.hour * 60 + picked.minute) - (now.hour * 60 + now.minute)) <= 1

    at.text_input(key="fs_symbol").input("ethusdt")
    day = now.date() - timedelta(days=1)
    at.date_input(key="fs_since_day").set_value(day)
    at.time_input(key="fs_since_time").set_value(datetime(2000, 1, 1, 14, 0).time())
    at.checkbox(key="fs_start_confirm").check()
    at.button(key="fs_start").click().run()
    assert state["fs_when"] == datetime(day.year, day.month, day.day, 6, 0, tzinfo=timezone.utc)  # 14:00 HKT
    assert not at.exception


def test_free_style_row_end_requires_confirmation_and_sends_summary(fake):
    calls, state = fake
    state["active"] = [SUI_ROW, ETH_ROW]
    at = run()
    at.button(key="fs_stop_test1_ETHUSDT").click().run()
    assert ("fs_stop", "ETHUSDT") not in calls
    at.checkbox(key="fs_stop_confirm_test1_ETHUSDT").check()
    at.button(key="fs_stop_test1_ETHUSDT").click().run()
    assert ("fs_stop", "ETHUSDT") in calls
    assert ("telegram", "🏁 free style 結束|RESULT") in calls and not at.exception


# ---------------------------------------------------------------- 策略:一般策略 → 填參數 → 預覽 → 開始

def fill_scale_in(at, symbol="wldusdt"):
    at.radio(key="new_strategy").set_value("scale_in_ladder").run()
    at.text_input(key="new_symbol").input(symbol).run()
    if "new_prices" in [t.key for t in at.text_input]:
        at.text_input(key="new_prices").input("0.5,0.49,0").run()
    return at


def test_new_scale_in_writes_params_previews_then_needs_typed_confirmation(fake):
    calls, _ = fake
    at = fill_scale_in(run())
    assert "new_start" not in buttons(at)  # 沒預覽 → 沒有開始按鈕
    at.button(key="new_prepare").click().run()
    [(_, strategy, symbol, settings, params)] = [c for c in calls if c[0] == "prepare"]
    assert strategy == "strategies/scale_in_ladder.yaml" and symbol == "WLDUSDT"
    assert "dry_run" not in settings and "testnet" not in settings  # 頁面只做真正的交易環境,由 actions 強制寫 false
    assert params["entry_prices"] == [0.5, 0.49, 0.0]
    assert ("preview", "live_scale_in_ladder_wldusdt.yaml") in calls

    at.button(key="new_start").click().run()  # 沒打確認字
    assert ("start", "live_scale_in_ladder_wldusdt.yaml", "") in calls
    at.text_input(key="new_typed").input("yes")
    at.button(key="new_start").click().run()
    assert ("start", "live_scale_in_ladder_wldusdt.yaml", "yes") in calls and not at.exception


def test_no_dry_run_or_testnet_switches_and_start_is_real_money(fake):
    """頁面只做真正的交易環境(2026-10-09 使用者要求):沒有 dry_run / testnet 開關,開始一律是真實資金、打 yes"""
    at = fill_scale_in(run())
    assert not {"new_dry", "new_testnet"} & {c.key for c in at.checkbox}
    at.button(key="new_prepare").click().run()
    start = [b for b in at.button if b.key == "new_start"][0]
    assert "真實資金" in start.label
    assert any("輸入 yes" in t.label for t in at.text_input if t.key == "new_typed")


def test_changing_params_after_preview_requires_a_new_preview(fake):
    at = fill_scale_in(run())
    at.button(key="new_prepare").click().run()
    assert "new_start" in buttons(at)
    at.text_input(key="new_prices").input("0.6,0.49,0").run()
    assert "new_start" not in buttons(at)
    assert any("重新" in w.value for w in at.warning)


def test_strategy_and_symbol_already_running_cannot_be_started_again(fake):
    calls, _ = fake
    at = fill_scale_in(run(), symbol="suiusdt")  # SUI 的 scale_in_ladder 已經在跑
    assert "new_prepare" not in buttons(at)
    assert any("已經在跑" in i.value for i in at.info)
    assert not [c for c in calls if c[0] == "prepare"]


# ---------------------------------------------------------------- 策略:進行中的列 → 改參數 / 停止 / 脫離

def test_running_row_param_change_needs_preview_then_typed_confirmation(fake):
    calls, _ = fake
    at = run()
    c = "live_execution_config"
    assert f"ctl_apply_{c}" not in buttons(at)
    at.text_input(key=f"ctl_prices_{c}").input("1.1,1.09,0")
    at.button(key=f"ctl_preview_{c}").click().run()
    assert ("ctl_preview", {"entry_prices": [1.1, 1.09, 0.0]}) in calls
    at.text_input(key=f"ctl_typed_{c}").input("no")
    at.button(key=f"ctl_apply_{c}").click().run()
    assert ("ctl_apply", {"entry_prices": [1.1, 1.09, 0.0]}, "no") in calls  # 原樣交給 control,它會拒絕
    at.button(key=f"ctl_preview_{c}").click().run()
    at.text_input(key=f"ctl_typed_{c}").input("yes")
    at.button(key=f"ctl_apply_{c}").click().run()
    assert ("ctl_apply", {"entry_prices": [1.1, 1.09, 0.0]}, "yes") in calls and not at.exception


def test_running_row_params_changed_after_preview_cannot_be_applied(fake):
    at = run()
    c = "live_execution_config"
    at.text_input(key=f"ctl_prices_{c}").input("1.1,1.09,0")
    at.button(key=f"ctl_preview_{c}").click().run()
    at.text_input(key=f"ctl_prices_{c}").input("1.0,1.09,0").run()
    assert f"ctl_apply_{c}" not in buttons(at)
    assert any("預覽之後改過" in w.value for w in at.warning)


def test_running_row_stop_only_with_STOP_typed(fake):
    calls, _ = fake
    at = run()
    c = "live_execution_config"
    at.text_input(key=f"stop_typed_{c}").input("stop")
    at.button(key=f"stop_btn_{c}").click().run()
    assert ("stop", "live_execution_config.yaml") not in calls
    at.text_input(key=f"stop_typed_{c}").input("STOP")
    at.button(key=f"stop_btn_{c}").click().run()
    assert ("stop", "live_execution_config.yaml") in calls and not at.exception


def test_running_row_detach_only_with_DETACH_typed(fake):
    calls, _ = fake
    at = run()
    c = "live_execution_config"
    at.text_input(key=f"detach_typed_{c}").input("detach")
    at.button(key=f"detach_btn_{c}").click().run()
    assert ("detach", "live_execution_config.yaml") not in calls
    at.text_input(key=f"detach_typed_{c}").input("DETACH")
    at.button(key=f"detach_btn_{c}").click().run()
    assert ("detach", "live_execution_config.yaml") in calls and not at.exception


# ---------------------------------------------------------------- 區間策略(2026-10-10)

BAND_CONFIG = """strategy_path: strategies/weekend_band_reversion.yaml
symbol_override: XRPUSDT
dry_run: false
testnet: false
position_sizing:
  mode: fixed_qty
  value: 10
strategy_overrides:
  band: {buy_pct: 0.3, sell_pct: 0.5}
"""
BAND_ROW = {"kind": "strategy", "config": "live_weekend_band_reversion_xrpusdt", "symbol": "XRPUSDT",
            "strategy": "weekend_band_reversion", "mode": "🔴 實盤", "started_at": NOW}


def test_band_form_asks_buy_and_sell_pct_only(fake):
    calls, _ = fake
    at = run()
    at.radio(key="new_strategy").set_value("weekend_band_reversion").run()
    at.text_input(key="new_symbol").input("xrpusdt").run()
    keys = {w.key for w in list(at.text_input) + list(at.number_input) + list(at.checkbox)}
    assert {"new_buy_pct", "new_sell_pct"} <= keys
    assert not {"new_prices", "new_loop", "new_adopt", "new_distance"} & keys
    at.number_input(key="new_buy_pct").set_value(0.3).run()
    at.number_input(key="new_sell_pct").set_value(0.5).run()
    at.button(key="new_prepare").click().run()
    [(_, strategy, symbol, settings, params)] = [c for c in calls if c[0] == "prepare"]
    assert strategy == "strategies/weekend_band_reversion.yaml" and symbol == "XRPUSDT"
    assert params == {"band": {"buy_pct": 0.3, "sell_pct": 0.5}}
    assert "adopt_existing_position" not in settings
    assert not at.exception


def test_running_band_row_offers_stop_only(fake):
    _, state = fake
    root = actions_root(fake)
    (root / "live_weekend_band_reversion_xrpusdt.yaml").write_text(BAND_CONFIG, encoding="utf-8")
    state["active"] = [BAND_ROW]
    at = run()
    c = BAND_ROW["config"]
    keys = set(buttons(at))
    assert f"stop_btn_{c}" in keys
    assert f"ctl_preview_{c}" not in keys and f"detach_btn_{c}" not in keys
    assert not at.exception


def actions_root(fake):
    return data.project_root()


def test_overview_shows_start_estimated_end_and_length_to_the_minute(fake, monkeypatch):
    """2026-10-10 使用者要求:總覽要有策略啟動時間、預估結束時間、預估長度;時間只到分鐘。"""
    import re
    from zoneinfo import ZoneInfo

    hkt = ZoneInfo("Asia/Hong_Kong")
    started = datetime(2026, 10, 7, 4, 16, 41, tzinfo=hkt)
    monkeypatch.setattr(data, "unfinished_runs", lambda url: {("TEST1", "SUIUSDT"): {
        "symbol": "SUIUSDT", "account": "test1", "strategy_name": "scale_in_ladder", "git_commit": "f251f84399",
        "started_at": started}})
    at = run()
    df = at.dataframe[0].value
    row = df[df["設定檔"] == "live_execution_config"].iloc[0]
    assert row["啟動時間"] == "10-07 04:16"
    assert row["預估結束"] == "10-12 05:55"
    assert row["預估長度"] == "5 天 1 小時 39 分"
    assert not any(re.search(r"\d\d:\d\d:\d\d", str(v)) for v in row.values)  # 沒有秒


# ---------------------------------------------------------------- 多帳戶(2026-10-12)

def test_overview_daemon_table_shows_the_account(fake):
    at = run()
    df = at.dataframe[0].value
    assert df[df["設定檔"] == "live_execution_config"].iloc[0]["帳戶"] == "test1"


def test_bybit_snapshot_follows_the_selected_account(fake):
    calls, _ = fake
    at = run()
    assert list(at.selectbox(key="snap_account").options) == ["test1", "MAIN"]
    at.selectbox(key="snap_account").set_value("MAIN").run()
    assert ("client", "MAIN") in calls and not at.exception


def test_free_style_start_uses_the_selected_account(fake):
    calls, _ = fake
    at = run()
    at.multiselect(key="new_accounts").set_value(["MAIN"]).run()
    at.radio(key="new_strategy").set_value("free style").run()
    at.text_input(key="fs_symbol").input("ethusdt")
    at.checkbox(key="fs_start_confirm").check()
    at.button(key="fs_start").click().run()
    assert ("fs_start", "ETHUSDT") in calls and ("fs_account", "MAIN") in calls
    assert ("client", "MAIN") in calls and not at.exception


def test_free_style_row_stops_on_its_own_account(fake):
    calls, state = fake
    state["active"] = [{**ETH_ROW, "account": "MAIN"}]
    at = run()
    assert any("MAIN" in e.label for e in at.expander)
    at.checkbox(key="fs_stop_confirm_MAIN_ETHUSDT").check()
    at.button(key="fs_stop_MAIN_ETHUSDT").click().run()
    assert ("fs_account", "MAIN") in calls and ("client", "MAIN") in calls and not at.exception


def test_new_strategy_is_prepared_for_the_selected_account(fake):
    calls, _ = fake
    at = run()
    at.multiselect(key="new_accounts").set_value(["MAIN"]).run()
    fill_scale_in(at)
    at.button(key="new_prepare").click().run()
    assert ("prepare_account", "MAIN") in calls and not at.exception


# ---------------------------------------------------------------- 一次多個帳戶(2026-10-12)

def test_accounts_default_to_test1_only(fake):
    at = run()
    ms = at.multiselect(key="new_accounts")
    assert list(ms.options) == ["test1", "MAIN"] and list(ms.value) == ["test1"]


def test_many_accounts_use_account_percentage_and_prepare_each(fake):
    calls, _ = fake
    at = run()
    at.multiselect(key="new_accounts").set_value(["test1", "MAIN"]).run()
    fill_scale_in(at)
    assert "new_mode" not in [s.key for s in at.selectbox]  # 多帳戶:數量模式固定權益百分比
    at.button(key="new_prepare").click().run()
    prepared = [c for c in calls if c[0] == "prepare"]
    assert len(prepared) == 2 and all(c[3]["position_sizing.mode"] == "account_percentage" for c in prepared)
    assert [c[1] for c in calls if c[0] == "prepare_account"] == ["test1", "MAIN"]
    assert not at.exception


def test_many_accounts_start_only_those_that_passed(fake, monkeypatch):
    calls, _ = fake
    monkeypatch.setattr(actions, "prepare_config", lambda root_, strategy, symbol, settings, params, **k: calls.append(
        ("prepare_account", k.get("account"))) or (data.project_root() / f"live_x_{k.get('account').lower()}.yaml"))
    monkeypatch.setattr(actions, "preflight_preview", lambda path: actions.PreviewResult(
        text=f"預覽 {Path(path).name}", ready="main" not in Path(path).name, fingerprint="fp", at=time.time()))
    at = run()
    at.multiselect(key="new_accounts").set_value(["test1", "MAIN"]).run()
    fill_scale_in(at)
    at.button(key="new_prepare").click().run()
    page = " ".join(str(m.value) for m in at.markdown) + " ".join(str(e.value) for e in at.error)
    assert "MAIN" in page
    at.text_input(key="new_typed").input("yes")
    at.button(key="new_start").click().run()
    assert [c for c in calls if c[0] == "start"] == [("start", "live_x_test1.yaml", "yes")]
    assert not at.exception


def test_free_style_on_many_accounts_starts_each(fake):
    calls, _ = fake
    at = run()
    at.multiselect(key="new_accounts").set_value(["test1", "MAIN"]).run()
    at.radio(key="new_strategy").set_value("free style").run()
    at.text_input(key="fs_symbol").input("ethusdt")
    at.checkbox(key="fs_start_confirm").check()
    at.button(key="fs_start").click().run()
    assert [c[1] for c in calls if c[0] == "fs_account"] == ["test1", "MAIN"] and not at.exception


# ---------------------------------------------------------------- 補記過去一段(2026-10-13)

@pytest.fixture
def backfill_calls(fake, monkeypatch):
    calls, _ = fake

    def preview(db, client, symbol, start, end, now, account=None, strategy_name="free style", **user):
        calls.append(("bf_preview", symbol, account, strategy_name, start, end))
        calls.append(("bf_user", user))
        return fs.BackfillPreview(symbol=symbol, account=account, strategy_name=strategy_name, start=start, end=end,
                                  orders=5, events=2, gross_pnl=3.0, trade_fees=0.64, open_position=0, warnings=[])

    monkeypatch.setattr(fs, "preview_backfill", preview)
    monkeypatch.setattr(fs, "existing_segments", lambda db, symbol, account, now: list(SEGMENTS))
    monkeypatch.setattr(fs, "backfill", lambda db, client, symbol, start, end, now, **k: calls.append(
        ("bf_write", symbol, k.get("account"), k.get("strategy_name"))) or calls.append(
        ("bf_write_user", {x: k.get(x) for x in ("expected_hold", "direction", "note", "target_price", "stop_price")}))
        or "RESULT")
    return calls


SEGMENTS = []


def fill_backfill(at, name="sat_weekend"):
    from datetime import date, time as dtime

    at.selectbox(key="bf_account").set_value("MAIN")
    at.text_input(key="bf_symbol").input("eth")
    at.text_input(key="bf_name").input(name)
    at.date_input(key="bf_start_date").set_value(date(2026, 10, 9))
    at.time_input(key="bf_start_time").set_value(dtime(10, 0))
    at.date_input(key="bf_end_date").set_value(date(2026, 10, 9))
    at.time_input(key="bf_end_time").set_value(dtime(11, 30))
    return at.run()


def test_backfill_defaults_to_free_style(backfill_calls):
    at = run()
    assert at.text_input(key="bf_name").value == "free style"


def test_backfill_preview_then_confirm_writes(backfill_calls):
    calls = backfill_calls
    at = fill_backfill(run())
    assert "bf_write" not in buttons(at)  # 沒預覽 → 沒有寫入按鈕
    at.button(key="bf_preview").click().run()
    [(_, symbol, account, name, start, end)] = [c for c in calls if c[0] == "bf_preview"]
    assert (symbol, account, name) == ("ETHUSDT", "MAIN", "sat_weekend")
    assert (start, end) == (datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc), datetime(2026, 10, 9, 3, 30, tzinfo=timezone.utc))
    at.button(key="bf_write").click().run()
    assert not [c for c in calls if c[0] == "bf_write"]  # 沒勾確認
    at.checkbox(key="bf_confirm").check()
    at.button(key="bf_write").click().run()
    assert ("bf_write", "ETHUSDT", "MAIN", "sat_weekend") in calls
    assert any(c[0] == "telegram" for c in calls) and not at.exception


def test_backfill_changed_after_preview_needs_a_new_preview(backfill_calls):
    calls = backfill_calls
    at = fill_backfill(run())
    at.button(key="bf_preview").click().run()
    at.text_input(key="bf_symbol").input("btc").run()
    assert "bf_write" not in buttons(at)


def test_backfill_error_is_shown(backfill_calls, monkeypatch):
    def refuse(*a, **k):
        raise fs.FreeStyleError("跟已有的紀錄重疊")

    monkeypatch.setattr(fs, "preview_backfill", refuse)
    at = fill_backfill(run())
    at.button(key="bf_preview").click().run()
    assert any("重疊" in str(e.value) for e in at.error) and "bf_write" not in buttons(at)


def test_backfill_lives_in_its_own_tab_and_free_style_start_does_not_say_backfill(fake):
    """2026-10-13 使用者分不清「現在開始記錄」和「補回過去」:補記放獨立分頁,free style 開始時間不叫「補記」"""
    at = run()
    backfill_tab, strategy_tab = at.tabs[4], at.tabs[3]
    assert "bf_preview" in [b.key for b in backfill_tab.button]
    assert "bf_preview" not in [b.key for b in strategy_tab.button]
    at.radio(key="new_strategy").set_value("free style").run()
    labels = [d.label for d in at.date_input if d.key.startswith("fs_")] + \
             [t.label for t in at.time_input if t.key.startswith("fs_")]
    assert labels and not any("補記" in label for label in labels)


def test_backfill_optional_fields_default_to_empty(backfill_calls):
    calls = backfill_calls
    at = fill_backfill(run())
    at.button(key="bf_preview").click().run()
    [user] = [c[1] for c in calls if c[0] == "bf_user"]
    assert user == {"expected_hold": None, "direction": None, "note": None, "target_price": None, "stop_price": None}
    assert at.selectbox(key="bf_direction").value == "自動(從成交判斷)"


def test_backfill_passes_hold_direction_and_note(backfill_calls):
    calls = backfill_calls
    at = fill_backfill(run())
    at.number_input(key="bf_hold_value").set_value(4.0)
    at.selectbox(key="bf_hold_unit").set_value("小時")
    at.selectbox(key="bf_direction").set_value("short")
    at.text_area(key="bf_note").input("週末試手感")
    at.number_input(key="bf_target").set_value(2400.0)
    at.number_input(key="bf_stop").set_value(2600.0).run()
    at.button(key="bf_preview").click().run()
    at.checkbox(key="bf_confirm").check()
    at.button(key="bf_write").click().run()
    expected = {"expected_hold": timedelta(hours=4), "direction": "short", "note": "週末試手感",
                "target_price": 2400.0, "stop_price": 2600.0}
    assert [c[1] for c in calls if c[0] == "bf_user"] == [expected]
    assert [c[1] for c in calls if c[0] == "bf_write_user"] == [expected] and not at.exception


@pytest.fixture
def busy(backfill_calls):
    """MAIN 的 ETHUSDT 在 10-09 10:30 → 11:00(HKT)已經有一段 scale_in_ladder。"""
    seg = fs.Segment("r1", "scale_in_ladder", datetime(2026, 10, 9, 2, 30, tzinfo=timezone.utc),
                     datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc), False)
    SEGMENTS[:] = [seg]
    yield backfill_calls
    SEGMENTS.clear()


def test_backfill_lists_existing_segments_and_marks_overlap_in_red(busy):
    at = fill_backfill(run())  # 10:00 → 11:30,把 10:30 → 11:00 整段包住
    page = " ".join(str(c.value) for c in at.caption) + " ".join(str(m.value) for m in at.markdown)
    assert "scale_in_ladder 10-09 10:30 → 10-09 11:00" in page
    assert any("重疊" in str(e.value) for e in at.error)
    assert at.button(key="bf_preview").disabled


def test_backfill_start_inside_a_segment_is_marked_on_the_start_field(busy):
    from datetime import time as dtime

    at = fill_backfill(run())
    at.time_input(key="bf_start_time").set_value(dtime(10, 45))
    at.time_input(key="bf_end_time").set_value(dtime(12, 0)).run()
    errors = [str(e.value) for e in at.error]
    assert any("開始重疊" in e for e in errors) and not any("結束重疊" in e for e in errors)


def test_backfill_free_window_can_be_previewed(busy):
    from datetime import time as dtime

    at = fill_backfill(run())
    at.time_input(key="bf_start_time").set_value(dtime(11, 0))
    at.time_input(key="bf_end_time").set_value(dtime(12, 0)).run()  # 剛好接在後面
    assert not any("重疊" in str(e.value) for e in at.error)
    assert not at.button(key="bf_preview").disabled


# ---------------------------------------------------------------- 每日對帳(2026-10-13)

@pytest.fixture
def unrecorded(fake):
    from strategy_lab.live import reconcile as rc

    UNRECORDED[:] = [rc.Unrecorded("MAIN", "ETHUSDT", datetime(2026, 10, 9, 11, 10, 30, tzinfo=timezone.utc),
                                   datetime(2026, 10, 9, 13, 51, 5, tzinfo=timezone.utc), 9, ["x"] * 9, 4)]
    yield fake
    UNRECORDED.clear()


def test_overview_lists_unrecorded_trades(unrecorded):
    at = run()
    tables = " ".join(df.value.to_csv() for df in at.dataframe)
    assert "MAIN" in tables and "10-09 19:10" in tables and "10-09 21:51" in tables


def test_prefill_fills_the_backfill_form_around_the_trades(unrecorded):
    from datetime import date, time as dtime

    at = run()
    at.button(key="unrec_fill_0").click().run()
    assert at.selectbox(key="bf_account").value == "MAIN"
    assert at.text_input(key="bf_symbol").value == "ETH"
    assert (at.date_input(key="bf_start_date").value, at.time_input(key="bf_start_time").value) == (date(2026, 10, 9), dtime(19, 10))
    assert (at.date_input(key="bf_end_date").value, at.time_input(key="bf_end_time").value) == (date(2026, 10, 9), dtime(21, 52))
    assert not at.exception


def test_reconcile_now_button(fake):
    calls, _ = fake
    at = run()
    at.button(key="reconcile_now").click().run()
    assert ("reconcile",) in calls and not at.exception


def test_unrecorded_table_lists_orders_and_fills(unrecorded):
    df = run().dataframe[-1].value
    assert list(df.columns)[-2:] == ["訂單數", "成交筆數"]
    assert (df.iloc[0]["訂單數"], df.iloc[0]["成交筆數"]) == (4, 9)


def test_unrecorded_newest_first(fake):
    """2026-10-13 使用者:補記從最近的日期排到最舊的(表格和補記按鈕都是)"""
    from strategy_lab.live import reconcile as rc

    old = rc.Unrecorded("MAIN", "ETHUSDT", datetime(2026, 9, 14, 20, 35, tzinfo=timezone.utc),
                        datetime(2026, 10, 7, 10, 7, tzinfo=timezone.utc), 24, ["a"], 9)
    new = rc.Unrecorded("MAIN", "ETHUSDT", datetime(2026, 10, 12, 14, 38, tzinfo=timezone.utc),
                        datetime(2026, 10, 13, 5, 23, tzinfo=timezone.utc), 17, ["b"], 6)
    UNRECORDED[:] = [old, new]
    try:
        at = run()
        assert list(at.dataframe[-1].value["第一筆(HKT)"]) == ["10-12 22:38", "09-15 04:35"]
        labels = [b.label for b in at.button if b.key.startswith("unrec_fill_")]
        assert "10-12 22:38" in labels[0] and "09-15 04:35" in labels[1]
    finally:
        UNRECORDED.clear()


def test_unrecorded_list_shows_last_update_on_top(unrecorded, monkeypatch):
    """2026-10-13 使用者:清單最頂加最新更新日期(= 上次對帳時間)"""
    from strategy_lab.live import reconcile as rc

    monkeypatch.setattr(rc, "last_run", lambda *a: datetime(2026, 10, 13, 10, 22, tzinfo=timezone.utc))
    at = run()
    texts = [str(m.value) for m in at.markdown]
    assert any("最新更新" in t and "10-13 18:22" in t for t in texts)
