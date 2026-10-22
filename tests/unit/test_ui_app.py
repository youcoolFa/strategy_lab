"""Streamlit 畫面(strategy_lab/ui/app.py)的測試:用 streamlit 的 AppTest 真的跑整頁,
資料層 / 動作層換成假的(不碰網路、資料庫、daemon)。

分頁:總覽 / 交易紀錄 / Log / 策略。「策略」分頁(2026-10-09 改版)= 選策略(free style 是其中一個)→
填參數 → 開始;上面是進行中清單(在跑的策略 + 進行中的 free style),每一列有自己的改參數 / 停止 / 交接 / 結束。
"""

import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
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

    def get_last_price(self, symbol):
        return 2516.0

    def get_fee_rates(self, symbol):
        return 0.0002, 0.00055


UNRECORDED = []
GRID_RETURN = {}  # 假 AG Grid 回傳的資料(模擬使用者在表格裡篩選 / 編輯之後)

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
    monkeypatch.setattr(data, "list_accounts", lambda: ["MAIN", "test1"])  # data.list_accounts 把 main 排第一
    monkeypatch.setattr(data, "git_head", lambda *a: "9cf3920")
    monkeypatch.setattr(data, "project_root", lambda: root)
    monkeypatch.setattr(data, "list_daemons", lambda *a: [data.DaemonRow(
        config="live_execution_config", symbol="SUIUSDT", pid=54931, alive=True,
        console_log=tmp_path / "c.log", last_log_at=NOW - timedelta(minutes=90))])
    monkeypatch.setattr(data, "unfinished_runs", lambda url: {("TEST1", "SUIUSDT"): {
        "symbol": "SUIUSDT", "account": "test1", "strategy_name": "scale_in_ladder", "git_commit": "f251f84399",
        "started_at": NOW}})
    monkeypatch.setattr(data, "recent_runs", lambda url, limit=20: [])
    monkeypatch.setattr(data, "pnl_strategies", lambda db, account: ["free style"])
    monkeypatch.setattr(data, "pnl_rows", lambda db, account, strategy: [])
    monkeypatch.setattr(data, "sat_weekends", lambda db, account: [])
    import st_aggrid

    monkeypatch.setattr(st_aggrid, "AgGrid", lambda df, **k: calls.append(("aggrid", df, k)) or
                        types.SimpleNamespace(data=GRID_RETURN.get("data", df)))
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
    monkeypatch.setattr(actions.daemon, "handover", lambda path: calls.append(("detach", Path(path).name)) or "已交接")
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
    # 2026-10-14 使用者:損益表第一、策略第二、交易紀錄第三、Log 第四(總覽放最後);補記分頁拿掉
    assert [t.label for t in at.tabs] == ["損益表", "sat_strategy", "策略", "交易紀錄", "Log", "總覽"]  # 10-22 加 sat_strategy


# ---------------------------------------------------------------- 總覽:背景程式表下面的 停止 / 結束(2026-10-17)
# 使用者要求:停止、結束鍵放在總覽的背景程式表旁邊,不用切到策略分頁。確認方式不變(打 STOP / 勾我確認),
# 只放停止與結束,不放交接;key 都加 ov_ 前綴,避免跟策略分頁同一支 daemon 的元件撞 key。

def test_overview_stop_requires_STOP_typed_for_an_alive_daemon(fake):
    calls, _ = fake
    at = run()
    c = "live_execution_config"
    assert not at.exception  # 兩個分頁都畫同一支 daemon:key 不能重複
    at.text_input(key=f"ov_stop_typed_{c}").input("stop")
    at.button(key=f"ov_stop_btn_{c}").click().run()
    assert ("stop", "live_execution_config.yaml") not in calls
    at.text_input(key=f"ov_stop_typed_{c}").input("STOP")
    at.button(key=f"ov_stop_btn_{c}").click().run()
    assert ("stop", "live_execution_config.yaml") in calls and not at.exception


def test_overview_offers_stop_but_not_handover(fake):
    at = run()
    keys = [b.key for b in at.button] + [t.key for t in at.text_input]
    assert "ov_stop_btn_live_execution_config" in keys
    assert not any(str(k).startswith("ov_handover") for k in keys)


def test_overview_has_no_stop_when_no_daemon_is_alive(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(data, "list_daemons", lambda *a: [data.DaemonRow(
        config="live_execution_config", symbol="SUIUSDT", pid=None, alive=False,
        console_log=tmp_path / "c.log", last_log_at=NOW - timedelta(minutes=90))])
    monkeypatch.setattr(data, "unfinished_runs", lambda url: {})
    at = run()
    assert not at.exception
    assert not any(str(b.key).startswith("ov_") for b in at.button)


def test_overview_free_style_end_requires_confirmation_and_sends_summary(fake, monkeypatch):
    calls, _ = fake
    monkeypatch.setattr(data, "unfinished_runs", lambda url: {("TEST1", "ETHUSDT"): {
        "symbol": "ETHUSDT", "account": "test1", "strategy_name": "free style", "git_commit": None, "started_at": NOW}})
    at = run()
    assert not at.exception
    at.button(key="ov_fs_stop_test1_ETHUSDT").click().run()
    assert ("fs_stop", "ETHUSDT") not in calls  # 沒勾我確認
    at.checkbox(key="ov_fs_stop_confirm_test1_ETHUSDT").check()
    at.button(key="ov_fs_stop_test1_ETHUSDT").click().run()
    assert ("fs_stop", "ETHUSDT") in calls
    assert ("telegram", "🏁 free style 結束|RESULT") in calls and not at.exception


# ---------------------------------------------------------------- 策略:選策略

def test_strategy_choices_are_every_strategy_yaml_plus_free_style(fake):
    at = run()
    options = list(at.radio(key="new_strategy").options)
    stems = sorted(p.stem for p in (ROOT / "strategies").glob("*.yaml"))
    assert options == stems + ["free style"]


# ---------------------------------------------------------------- 策略:free style

def test_free_style_start_needs_no_confirmation(fake):
    """free style 只記帳,開始不用勾確認,按下去就寫進 trading 資料庫(2026-10-12)。"""
    calls, _ = fake
    at = run()
    at.radio(key="new_strategy").set_value("free style").run()
    assert "fs_start_confirm" not in [c.key for c in at.checkbox]
    at.button(key="fs_start").click().run()
    assert not any(c[0] == "fs_start" for c in calls)  # 沒填幣種
    at.text_input(key="fs_symbol").input("ethusdt")
    at.button(key="fs_start").click().run()
    assert ("fs_start", "ETHUSDT") in calls and not at.exception




def test_free_style_symbol_only_needs_the_coin(fake):
    calls, _ = fake
    at = run()
    at.radio(key="new_strategy").set_value("free style").run()
    at.text_input(key="fs_symbol").input("eth").run()
    assert any("ETHUSDT" in c.value for c in at.caption)
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
    at = run()
    at.multiselect(key="new_accounts").set_value(["test1"]).run()  # 在跑的那支設定檔沒寫 account = test1
    at = fill_scale_in(at, symbol="suiusdt")  # SUI 的 scale_in_ladder 已經在跑
    assert "new_prepare" not in buttons(at)
    assert any("已經在跑" in i.value for i in at.info)
    assert not [c for c in calls if c[0] == "prepare"]


# ---------------------------------------------------------------- 策略:進行中的列 → 改參數 / 停止 / 交接

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


def test_running_row_handover_only_with_HANDOVER_typed(fake):
    calls, _ = fake
    at = run()
    c = "live_execution_config"
    at.text_input(key=f"handover_typed_{c}").input("detach")
    at.button(key=f"handover_btn_{c}").click().run()
    assert ("detach", "live_execution_config.yaml") not in calls
    at.text_input(key=f"handover_typed_{c}").input("HANDOVER")
    at.button(key=f"handover_btn_{c}").click().run()
    assert ("detach", "live_execution_config.yaml") in calls and not at.exception


# ---------------------------------------------------------------- 區間策略(2026-10-10)

BAND_CONFIG = """strategy_path: strategies/sat_strategy.yaml
symbol_override: XRPUSDT
dry_run: false
testnet: false
position_sizing:
  mode: fixed_qty
  value: 10
strategy_overrides:
  band: {buy_pct: 0.3, sell_pct: 0.5}
"""
BAND_ROW = {"kind": "strategy", "config": "live_sat_strategy_xrpusdt", "symbol": "XRPUSDT",
            "strategy": "sat_strategy", "mode": "🔴 實盤", "started_at": NOW}


def test_band_form_asks_buy_and_sell_pct_only(fake):
    calls, _ = fake
    at = run()
    at.radio(key="new_strategy").set_value("sat_strategy").run()
    at.text_input(key="new_symbol").input("xrpusdt").run()
    keys = {w.key for w in list(at.text_input) + list(at.number_input) + list(at.checkbox)}
    assert {"new_buy_pct", "new_sell_pct"} <= keys
    assert not {"new_prices", "new_loop", "new_adopt", "new_distance"} & keys
    at.number_input(key="new_buy_pct").set_value(0.3).run()
    at.number_input(key="new_sell_pct").set_value(0.5).run()
    at.button(key="new_prepare").click().run()
    [(_, strategy, symbol, settings, params)] = [c for c in calls if c[0] == "prepare"]
    assert strategy == "strategies/sat_strategy.yaml" and symbol == "XRPUSDT"
    assert params == {"band": {"buy_pct": 0.3, "sell_pct": 0.5}}
    assert "adopt_existing_position" not in settings
    assert not at.exception



def test_every_non_band_strategy_offers_long_and_short(fake):
    """每個策略都能做多做空(2026-10-12):預設 = 策略 YAML 的方向,選的值寫進設定檔。"""
    calls, _ = fake
    at = run()
    at.radio(key="new_strategy").set_value("weekend_short_breakout").run()
    at.text_input(key="new_symbol").input("eth").run()
    radio = at.radio(key="new_direction")
    assert list(radio.options) == ["long 做多", "short 做空"] and radio.value == "short"
    radio.set_value("long").run()
    at.button(key="new_prepare").click().run()
    [(_, _, _, _, params)] = [c for c in calls if c[0] == "prepare"]
    assert params["direction"] == "long" and not at.exception


def test_band_strategy_has_no_direction_choice(fake):
    at = run()
    at.radio(key="new_strategy").set_value("sat_strategy").run()
    at.text_input(key="new_symbol").input("xrp").run()
    assert "new_direction" not in [r.key for r in at.radio]

def test_running_band_row_offers_stop_only(fake):
    _, state = fake
    root = actions_root(fake)
    (root / "live_sat_strategy_xrpusdt.yaml").write_text(BAND_CONFIG, encoding="utf-8")
    state["active"] = [BAND_ROW]
    at = run()
    c = BAND_ROW["config"]
    keys = set(buttons(at))
    assert f"stop_btn_{c}" in keys
    assert f"ctl_preview_{c}" not in keys and f"handover_btn_{c}" not in keys
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
    assert list(at.selectbox(key="snap_account").options) == ["MAIN", "test1"]
    at.selectbox(key="snap_account").set_value("test1").run()
    assert ("client", "test1") in calls and not at.exception


def test_free_style_start_uses_the_selected_account(fake):
    calls, _ = fake
    at = run()
    at.multiselect(key="new_accounts").set_value(["MAIN"]).run()
    at.radio(key="new_strategy").set_value("free style").run()
    at.text_input(key="fs_symbol").input("ethusdt")
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

def test_accounts_default_to_main_only(fake):
    """頁面上預設選 main(2026-10-12);總覽的帳戶也預設 main。"""
    at = run()
    ms = at.multiselect(key="new_accounts")
    assert list(ms.options) == ["MAIN", "test1"] and list(ms.value) == ["MAIN"]
    assert at.selectbox(key="snap_account").value == "MAIN"


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
    at.button(key="fs_start").click().run()
    assert [c[1] for c in calls if c[0] == "fs_account"] == ["test1", "MAIN"] and not at.exception


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
    finally:
        UNRECORDED.clear()


def test_unrecorded_list_shows_last_update_on_top(unrecorded, monkeypatch):
    """2026-10-13 使用者:清單最頂加最新更新日期(= 上次對帳時間)"""
    from strategy_lab.live import reconcile as rc

    monkeypatch.setattr(rc, "last_run", lambda *a: datetime(2026, 10, 13, 10, 22, tzinfo=timezone.utc))
    at = run()
    texts = [str(m.value) for m in at.markdown]
    assert any("最新更新" in t and "10-13 18:22" in t for t in texts)


# ---------------------------------------------------------------- 損益表(2026-10-14)

PNL_ROW = {"run_id": "r1", "event_index": 1, "日期": "2026/10/09", "幣種": "ETHUSDT", "方向": "short", "數量": 13.0,
           "建倉價格": 2513.0, "建倉時間": "10-08 21:36", "平倉價格": 2470.0, "真實損益": 450.0, "手續費": 30.0,
           "手續費佔比": 6.25, "預估時長": "", "實際時長": "8 小時 3 分", "目標價": None, "停損價": None, "備註": "",
           "實際秒數": 29000, "預估秒數": None}
PNL_COLUMNS = ["日期", "幣種", "方向", "數量", "建倉價格", "建倉時間", "平倉價格", "真實損益", "手續費", "手續費佔比(%)",
               "預估時長", "預估單位", "實際時長", "目標價", "停損價", "備註"]


@pytest.fixture
def test_backfill_tab_is_gone_and_failures_point_to_the_command(unrecorded):
    """2026-10-14 使用者:補記分頁拿掉(對帳會自動記成 free style);記不了的列在總覽,附終端機補記指令"""
    at = run()
    assert "補記" not in [t.label for t in at.tabs]
    assert not [b for b in at.button if b.key.startswith(("unrec_fill_", "bf_"))]
    text = " ".join(str(c.value) for c in at.caption) + " ".join(str(c.value) for c in at.code)
    assert "free_style backfill" in text
    at.radio(key="new_strategy").set_value("free style").run()
    assert not any("「補記」分頁" in str(c.value) for c in at.caption)


# ---------------------------------------------------------------- 損益表(2026-10-14,AG Grid 免費版)

PNL_ROW = {"run_id": "r1", "event_index": 1, "日期": "2026/10/09", "幣種": "ETHUSDT", "方向": "short", "數量": 13.0,
           "建倉價格": 2513.0, "建倉時間": "10-08 21:36", "平倉價格": 2470.0, "真實損益": 450.0, "手續費": 30.0,
           "手續費佔比": 6.25, "預估時長": None, "預估單位": "", "實際時長": "8 小時 3 分", "目標價": None, "停損價": None,
           "備註": "", "實際秒數": 29000, "預估秒數": None}
LONG_ROW = {**PNL_ROW, "run_id": "r2", "方向": "long", "日期": "2026/10/07", "真實損益": -6.0, "手續費": 2.0}
PNL_COLUMNS = ["日期", "幣種", "方向", "數量", "建倉價格", "建倉時間", "平倉價格", "真實損益", "手續費", "手續費佔比(%)",
               "預估時長", "預估單位", "實際時長", "目標價", "停損價", "備註"]


@pytest.fixture
def pnl(fake, monkeypatch):
    calls, _ = fake
    monkeypatch.setattr(data, "pnl_strategies", lambda db, account: ["free style", "scale_in_ladder"])
    monkeypatch.setattr(data, "pnl_rows", lambda db, account, strategy: calls.append(("pnl_rows", account, strategy))
                        or [dict(PNL_ROW), dict(LONG_ROW)])
    monkeypatch.setattr(actions, "save_pnl_edits", lambda db, edits: calls.append(("pnl_save", edits)) or len(edits))
    GRID_RETURN.clear()
    yield calls
    GRID_RETURN.clear()


def grid_call(calls):
    return [c for c in calls if c[0] == "aggrid"][-1]


def test_pnl_defaults_and_the_grid_gets_every_column(pnl):
    at = run()
    assert (at.selectbox(key="pnl_account").value, at.selectbox(key="pnl_strategy").value) == ("MAIN", "free style")
    _, df, kwargs = grid_call(pnl)
    assert list(df.columns)[:len(PNL_COLUMNS)] == PNL_COLUMNS
    assert {"run_id", "event_index", "實際秒數", "預估秒數"} <= set(df.columns)  # 隱藏,存檔 / 總結用
    assert kwargs["data_return_mode"] == "FILTERED_AND_SORTED" and kwargs["allow_unsafe_jscode"] is True
    assert "pnl_f_dirs" not in [m.key for m in at.multiselect]  # 上方那排篩選拿掉,改在欄位標題
    assert not at.exception


SUMMARY_COLUMNS = ["總結", "勝率", "數量(平均)", "真實損益(總和)", "手續費(總和)", "手續費佔比(%,平均)", "實際時長(平均)",
                   "預估時長(平均)"]


def test_totals_and_summary_follow_what_the_grid_shows(pnl):
    """在表格裡篩選後(假 AG Grid 只回傳 long 那一列),合計和總結列跟著變"""
    GRID_RETURN["data"] = pd.DataFrame([{**LONG_ROW, "手續費佔比(%)": LONG_ROW["手續費佔比"]}])
    at = run()
    metrics = {m.label: m.value for m in at.tabs[0].metric}
    assert (metrics["交易對數"], metrics["真實損益合計(USDT)"], metrics["手續費合計(USDT)"]) == ("1", "-6", "2")
    summary = at.tabs[0].dataframe[0].value.iloc[0]
    assert (summary["總結"], summary["真實損益(總和)"], summary["手續費(總和)"]) == ("1 對", "-6", "2")


def test_summary_has_only_useful_columns_and_win_rate(pnl):
    """2026-10-21 使用者:總結表去掉沒用(空白)的欄位,加勝率"""
    at = run()  # 一賺(450)一虧(-6)
    summary = at.tabs[0].dataframe[0].value
    assert list(summary.columns) == SUMMARY_COLUMNS
    assert summary.iloc[0]["勝率"] == "50.0%"


def test_win_rate_is_a_big_number_on_top(pnl):
    at = run()
    metrics = {m.label: m.value for m in at.tabs[0].metric}
    assert metrics["勝率"] == "50.0%"
    assert [m.label for m in at.tabs[0].metric][:2] == ["交易對數", "勝率"]

def test_save_sends_only_edited_rows(pnl):
    edited = {**LONG_ROW, "手續費佔比(%)": 6.25, "預估時長": 4, "預估單位": "小時", "目標價": 2600.0, "備註": "照計劃"}
    GRID_RETURN["data"] = pd.DataFrame([{**PNL_ROW, "手續費佔比(%)": 6.25}, edited])
    at = run()
    at.button(key="pnl_save").click().run()
    [(_, edits)] = [c for c in pnl if c[0] == "pnl_save"]
    assert edits == [{"run_id": "r2", "event_index": 1, "預估時長": 4, "預估單位": "小時", "目標價": 2600.0, "停損價": None,
                      "備註": "照計劃"}]
    assert any("1 列" in str(x.value) for x in at.success)


def test_save_without_changes_says_so(pnl):
    at = run()
    at.button(key="pnl_save").click().run()
    assert not [c for c in pnl if c[0] == "pnl_save"] and any("沒有修改" in str(x.value) for x in at.info)


def test_save_error_is_shown(pnl, monkeypatch):
    GRID_RETURN["data"] = pd.DataFrame([{**PNL_ROW, "手續費佔比(%)": 6.25, "目標價": 2400.0, "停損價": 2600.0}])

    def refuse(db, edits):
        raise ValueError("第 1 列:做空時停損價要高於目標價")

    monkeypatch.setattr(actions, "save_pnl_edits", refuse)
    at = run()
    at.button(key="pnl_save").click().run()
    assert any("停損價要高於目標價" in str(e.value) for e in at.error)


def test_grid_filtered_to_nothing_says_so(pnl):
    GRID_RETURN["data"] = pd.DataFrame(columns=PNL_COLUMNS)
    at = run()
    assert any("篩選後沒有交易" in str(c.value) for c in at.tabs[0].caption) and not at.exception


def test_pnl_db_error_is_shown_not_crashing(fake, monkeypatch):
    def broken(db, account):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(data, "pnl_strategies", broken)
    at = run()
    assert not at.exception and any("connection refused" in str(e.value) for e in at.tabs[0].error)


def test_pnl_shows_round_trip_fee_in_points_for_the_selected_account(pnl):
    """ETHUSDT 現價 × maker 費率 × 2(買 + 賣)= 1.01 點;費率用上面選的帳戶"""
    at = run()
    metrics = {m.label: m.value for m in at.tabs[0].metric}
    assert metrics["手續費點數(ETHUSDT,maker 買+賣)"] == "1.01 點"
    assert ("client", "MAIN") in pnl


def test_pnl_fee_points_error_does_not_break_the_page(pnl, monkeypatch):
    def broken(client, symbol="ETHUSDT"):
        raise RuntimeError("timeout")

    monkeypatch.setattr(data, "fee_points", broken)
    at = run()
    metrics = {m.label: m.value for m in at.tabs[0].metric}
    assert metrics["手續費點數(ETHUSDT,maker 買+賣)"] == "—" and not at.exception


def test_pnl_update_button_reconciles_now(pnl):
    """2026-10-14 使用者:剛做完一筆手動交易,在損益表手動按更新 → 馬上對帳,新的一對自動記成 free style"""
    at = run()
    at.button(key="pnl_update").click().run()
    assert ("reconcile",) in pnl and not at.exception
    assert any("更新完成" in str(x.value) for x in at.tabs[0].success)


SAT_ROW = {"run_id": "r-1017", "週末": "2026/10/17(六)", "開始": "10-17 04:00", "結束": "10-19 05:55",
           "起點價": "81546 → 81667.6", "掛單價": "買 80934.4 → 買 81259.3 / 賣 82892.6", "方向": "long → both", "輪數": 2,
           "數量": 0.001, "真實損益": 0.19, "手續費": 0.05, "實際時長": "2 天", "結束原因": "時間窗收尾", "執行次數": 2,
           "來源": "舊程式 → strategy_lab", "備註": ""}


def test_sat_strategy_tab_defaults_to_test1_one_row_per_weekend_and_saves_notes(fake, monkeypatch):
    calls, _ = fake
    monkeypatch.setattr(data, "sat_weekends", lambda db, account: calls.append(("sat", account)) or [dict(SAT_ROW)])
    monkeypatch.setattr(actions, "save_sat_notes", lambda db, edits: calls.append(("sat_save", edits)) or len(edits))
    at = run()
    assert at.selectbox(key="sat_account").value == "test1" and ("sat", "test1") in calls
    assert {m.label for m in at.metric} >= {"週末數", "有成交的週末", "勝率"}
    at.button(key="sat_save").click().run()
    assert not [c for c in calls if c[0] == "sat_save"]  # 沒改 → 不存


def test_sat_strategy_weekend_without_trades_shows_blank_qty(fake, monkeypatch):
    calls, _ = fake
    monkeypatch.setattr(data, "sat_weekends", lambda db, account: [dict(SAT_ROW), dict(SAT_ROW, run_id="r2", 輪數=0, 數量=None)])
    at = run()
    df = next(d for d in at.dataframe if "數量" in getattr(d.value, "columns", []))
    assert list(df.value["數量"]) == ["0.001", ""] and not at.exception
    assert not at.exception
