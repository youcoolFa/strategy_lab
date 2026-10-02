"""端到端重現 2026-09-27 的事故情境:一個真的 process 收到 SIGHUP(關掉
終端機/PyCharm 時送出的訊號)。修正前 process 直接死掉、收尾沒跑;修正後
要走跟 Ctrl+C 一樣的收尾流程才結束。"""

import signal
import subprocess
import sys
import time
from pathlib import Path

CHILD = r"""
import sys, time
from strategy_lab.live.main import install_stop_signal_handlers

marker = sys.argv[1]

class Runner:
    stopped = False
    def request_stop(self):
        self.stopped = True

runner = Runner()
install_stop_signal_handlers(runner)
open(marker, "w").write("running\n")
while not runner.stopped:
    time.sleep(0.05)
open(marker, "a").write("cleanup-ran\n")
"""


def test_sighup_leads_to_cleanup_instead_of_silent_death(tmp_path):
    marker = tmp_path / "marker.txt"
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(marker)],
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists(), proc.stdout.read().decode(errors="replace") if proc.poll() is not None else "child did not start"

    proc.send_signal(signal.SIGHUP)
    proc.wait(timeout=10)

    assert proc.returncode == 0
    assert "cleanup-ran" in marker.read_text()
