"""頁面按鈕 → 真的設定檔 + 真的背景程式(2026-10-09,配合「策略」分頁改版)。

從 Streamlit 頁面(AppTest)走完整流程:選策略 scale_in_ladder → 填幣種與建倉價 →「寫入參數並預覽」
(真的 actions.prepare_config:從範本建立 live_scale_in_ladder_xrpusdt.yaml 並寫入參數,一律真錢)→ 打 yes →
開始(真的 daemon.start:開背景程式、寫 pid 檔)→ 進行中清單出現這一列 → 打 STOP(真的 daemon.stop:
送 SIGTERM、等它收尾結束、清掉 pid 檔)。脫離(DETACH)送 SIGUSR1。

換掉的只有:背景跑的程式(tests/integration/_fake_live_program.py,收到訊號就結束,不連交易所、
不下單)、preflight(不查市場)。設定檔 / pid 檔 / log 都在臨時資料夾,不碰真的 live_*.yaml、run/、logs/。
"""

import functools
import os
import signal
import sys
import time
from pathlib import Path

import pytest
import yaml

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from strategy_lab.live import control, daemon  # noqa: E402
from strategy_lab.live import free_style as fs  # noqa: E402
from strategy_lab.ui import actions, data  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
APP = str(ROOT / "strategy_lab" / "ui" / "app.py")
FAKE_PROGRAM = Path(__file__).with_name("_fake_live_program.py")
CONFIG_NAME = "live_scale_in_ladder_xrpusdt"


class FakeClient:
    def get_account_equity(self):
        return 100.0

    def list_positions(self):
        return []

    def list_open_orders(self):
        return []


@pytest.fixture
def project(tmp_path, monkeypatch):
    import streamlit as st

    st.cache_data.clear()  # 頁面的 30 秒快取在同一個 process 裡共用,不清會讀到上一個測試的假帳戶
    root = tmp_path / "proj"
    (root / "run").mkdir(parents=True)
    (root / "logs").mkdir()
    run_dir, log_dir = root / "run", root / "logs"

    real_start, real_stop, real_detach = daemon.start, daemon.stop, daemon.detach

    def start(config):
        config = Path(config).resolve()  # daemon.stop 會檢查指令裡有設定檔路徑
        return real_start(config, run_dir=run_dir, log_dir=log_dir, startup_wait=1.0,
                          command=[sys.executable, str(FAKE_PROGRAM), "--config", str(config)])

    monkeypatch.setattr(daemon, "start", start)
    monkeypatch.setattr(daemon, "stop", functools.partial(real_stop, run_dir=run_dir, timeout=15))
    monkeypatch.setattr(daemon, "detach", functools.partial(real_detach, run_dir=run_dir, timeout=15))
    monkeypatch.setattr(actions, "daemon_running", lambda path: control.is_running(Path(path), run_dir))

    monkeypatch.setattr(data, "load_env", lambda: None)
    monkeypatch.setattr(data, "db_url", lambda: None)
    monkeypatch.setattr(data, "make_client", lambda: FakeClient())
    monkeypatch.setattr(data, "git_head", lambda *a: "abc1234")
    monkeypatch.setattr(data, "project_root", lambda: root)
    real_list = data.list_daemons
    monkeypatch.setattr(data, "list_daemons", lambda *a: real_list(root))
    real_active = data.active_runs
    monkeypatch.setattr(data, "active_runs", lambda url, r=None: real_active(None, root))

    def fake_preflight(argv, input_fn, print_fn, start_fn):
        """跟真的 preflight 一樣:實盤問「輸入 yes」,答對才呼叫 start_fn(= daemon.start)。"""
        print_fn("【掛單計畫】(假的)")
        if input_fn("輸入 yes 確認以真實資金啟動: ").strip().lower() != "yes":
            print_fn("已取消,沒有啟動。")
            return 0
        info = start_fn(Path(argv[1]))
        print_fn(f"已在背景啟動(PID {info.pid}),終端機輸出: {info.console_log}")
        return 0

    monkeypatch.setattr(actions.preflight, "main", fake_preflight)
    monkeypatch.setattr(fs, "start", lambda *a, **k: pytest.fail("不該碰 free style"))

    yield {"root": root, "cfg": root / f"{CONFIG_NAME}.yaml", "pid_file": run_dir / f"{CONFIG_NAME}.pid",
           "console": log_dir / f"{CONFIG_NAME}.console.log"}

    pid = daemon._read_pid(run_dir / f"{CONFIG_NAME}.pid")  # 測試失敗也不留下孤兒程式
    if pid and daemon.is_alive(pid):
        os.kill(pid, signal.SIGKILL)


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def fill_and_preview(at):
    at.radio(key="new_strategy").set_value("scale_in_ladder").run()
    at.text_input(key="new_symbol").input("xrpusdt").run()
    at.text_input(key="new_prices").input("0.5,0.49,0").run()
    at.button(key="new_prepare").click().run()
    assert not at.exception
    return at


def start_from_the_page(p):
    at = fill_and_preview(AppTest.from_file(APP, default_timeout=30).run())
    cfg = yaml.safe_load(p["cfg"].read_text(encoding="utf-8"))  # 真的從範本建立並寫入
    assert cfg["strategy_path"] == "strategies/scale_in_ladder.yaml" and cfg["symbol_override"] == "XRPUSDT"
    assert cfg["entry_prices"] == [0.5, 0.49, 0]
    assert cfg["dry_run"] is False and cfg["testnet"] is False  # 頁面只做真正的交易環境(背景跑的是假程式)

    at.text_input(key="new_typed").input("yes")
    at.button(key="new_start").click().run()
    assert not at.exception
    assert any("已在背景開始" in s.value for s in at.success)
    pid = daemon._read_pid(p["pid_file"])
    assert pid and daemon.is_alive(pid)  # 真的有一個背景程式在跑
    at.run()  # 重整頁面
    assert any("XRPUSDT" in e.label and "scale_in_ladder" in e.label for e in at.expander)  # 進行中清單
    return at, pid


def test_choose_fill_preview_start_then_stop_runs_a_real_background_process(project):
    p = project
    at, pid = start_from_the_page(p)
    at.text_input(key=f"stop_typed_{CONFIG_NAME}").input("STOP")
    at.button(key=f"stop_btn_{CONFIG_NAME}").click().run()
    assert not at.exception
    assert any("已停止" in s.value for s in at.success)
    assert wait_until(lambda: not daemon.is_alive(pid))
    assert not p["pid_file"].exists()
    assert "收尾完成" in p["console"].read_text(encoding="utf-8")  # 走的是收尾(SIGTERM)
    at.run()
    assert not any("XRPUSDT" in e.label for e in at.expander)


def test_detach_from_the_page_ends_without_cleanup(project):
    p = project
    at, pid = start_from_the_page(p)
    at.text_input(key=f"detach_typed_{CONFIG_NAME}").input("DETACH")
    at.button(key=f"detach_btn_{CONFIG_NAME}").click().run()
    assert any("已脫離" in s.value for s in at.success)
    assert wait_until(lambda: not daemon.is_alive(pid))
    log = p["console"].read_text(encoding="utf-8")
    assert "脫離:不收尾" in log and "收尾完成" not in log


def test_wrong_confirmation_starts_nothing(project):
    p = project
    at = fill_and_preview(AppTest.from_file(APP, default_timeout=30).run())
    at.text_input(key="new_typed").input("ok")
    at.button(key="new_start").click().run()
    assert not p["pid_file"].exists()
    assert not any("XRPUSDT" in e.label for e in at.expander)
