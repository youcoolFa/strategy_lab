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


SUI_ROW = {"kind": "strategy", "config": "live_execution_config", "symbol": "SUIUSDT", "strategy": "scale_in_ladder",
           "mode": "🔴 實盤", "started_at": NOW}
ETH_ROW = {"kind": "free style", "config": None, "symbol": "ETHUSDT", "strategy": "free style",
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
    monkeypatch.setattr(data, "make_client", lambda: FakeClient())
    monkeypatch.setattr(data, "git_head", lambda *a: "9cf3920")
    monkeypatch.setattr(data, "project_root", lambda: root)
    monkeypatch.setattr(data, "list_daemons", lambda *a: [data.DaemonRow(
        config="live_execution_config", symbol="SUIUSDT", pid=54931, alive=True,
        console_log=tmp_path / "c.log", last_log_at=NOW - timedelta(minutes=90))])
    monkeypatch.setattr(data, "unfinished_by_symbol", lambda url: {"SUIUSDT": {
        "symbol": "SUIUSDT", "strategy_name": "scale_in_ladder", "git_commit": "f251f84399", "started_at": NOW}})
    monkeypatch.setattr(data, "recent_runs", lambda url, limit=20: [])
    monkeypatch.setattr(data, "recent_events", lambda url, limit=20: [])
    monkeypatch.setattr(data, "active_runs", lambda url, root=None: list(state["active"]))

    monkeypatch.setattr(fs, "start", lambda *a, **k: calls.append(("fs_start", a[2])) or "run-1")
    monkeypatch.setattr(fs, "stop", lambda *a, **k: calls.append(("fs_stop", a[2])) or "RESULT")
    monkeypatch.setattr(fs, "summary_message", lambda r: f"🏁 free style 結束|{r}")
    monkeypatch.setattr(fs, "send_telegram", lambda text: calls.append(("telegram", text)))

    monkeypatch.setattr(actions, "daemon_running", lambda path: Path(path).name == "live_execution_config.yaml")
    monkeypatch.setattr(actions, "prepare_config", lambda root_, strategy, symbol, settings, params, **k: calls.append(
        ("prepare", strategy, symbol, settings, params)) or (root / f"live_{Path(strategy).stem}_{symbol.lower()}.yaml"))
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
    monkeypatch.setattr(data, "make_client", lambda: made.append(1) or FakeClient())
    at = run()
    before = len(made)
    at.button(key="refresh").click().run()
    assert len(made) > before and not at.exception


def test_tabs(fake):
    at = run()
    assert [t.label for t in at.tabs] == ["總覽", "交易紀錄", "Log", "策略"]


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


def test_free_style_row_end_requires_confirmation_and_sends_summary(fake):
    calls, state = fake
    state["active"] = [SUI_ROW, ETH_ROW]
    at = run()
    at.button(key="fs_stop_ETHUSDT").click().run()
    assert ("fs_stop", "ETHUSDT") not in calls
    at.checkbox(key="fs_stop_confirm_ETHUSDT").check()
    at.button(key="fs_stop_ETHUSDT").click().run()
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
    monkeypatch.setattr(data, "unfinished_by_symbol", lambda url: {"SUIUSDT": {
        "symbol": "SUIUSDT", "strategy_name": "scale_in_ladder", "git_commit": "f251f84399", "started_at": started}})
    at = run()
    df = at.dataframe[0].value
    row = df[df["設定檔"] == "live_execution_config"].iloc[0]
    assert row["啟動時間"] == "10-07 04:16"
    assert row["預估結束"] == "10-12 05:55"
    assert row["預估長度"] == "5 天 1 小時 39 分"
    assert not any(re.search(r"\d\d:\d\d:\d\d", str(v)) for v in row.values)  # 沒有秒
