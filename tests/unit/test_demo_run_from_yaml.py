"""demo/run_from_yaml.py 裡寫死的策略名稱(起始時間 / tick 間隔對照表)都要真的存在於 strategies/
(weekend_mean_reversion 刪除時漏改會讓 demo 找不到設定,2026-10-10)。"""

from pathlib import Path

from demo import run_from_yaml

STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "strategies"


def test_every_named_strategy_exists():
    for name in set(run_from_yaml.START_TIMES) | set(run_from_yaml.TICK_INTERVALS):
        assert (STRATEGIES_DIR / f"{name}.yaml").exists(), name


def test_both_tables_cover_the_same_strategies():
    assert set(run_from_yaml.START_TIMES) == set(run_from_yaml.TICK_INTERVALS)
