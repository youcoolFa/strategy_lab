"""每日對帳的 launchd 排程(2026-10-13):每天 08:00 HKT,前景執行 .venv 的 Python。"""

import plistlib
from pathlib import Path

PLIST = Path(__file__).resolve().parents[2] / "scripts" / "com.strategylab.reconcile.plist"


def test_daily_at_0800_runs_the_reconcile_module_in_the_foreground():
    p = plistlib.loads(PLIST.read_bytes())
    assert p["Label"] == "com.strategylab.reconcile"
    assert p["ProgramArguments"] == ["/Users/mac/strategy_lab/.venv/bin/python", "-m", "strategy_lab.live.reconcile"]
    assert p["WorkingDirectory"] == "/Users/mac/strategy_lab"
    assert p["StartCalendarInterval"] == {"Hour": 8, "Minute": 0}  # 每天(沒有 Weekday)
    assert p["StandardOutPath"].startswith("/Users/mac/strategy_lab/logs/")
