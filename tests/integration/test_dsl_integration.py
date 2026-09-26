"""驗證 YAML 載入出來的策略,接進真正的 StrategyRunner 之後,行為跟
tests/integration/test_runner_integration.py 手動組裝版本完全一致——
證明 DSL 不只是「結構長得一樣」(那是 unit test 驗證的),接進完整的
狀態機也真的能跑完一次進出場循環。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from strategy_lab.dsl.loader import load_strategy
from strategy_lab.engine.runner import RunState, StrategyRunner

STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "strategies"


class TestWeekendStrategyFromYaml:
    def test_entry_fill_exit_fill_then_cleanup(self):
        strategy = load_strategy(STRATEGIES_DIR / "weekend_mean_reversion.yaml")
        runner = StrategyRunner(
            entry=strategy.entry,
            exit=strategy.exit,
            time_window=strategy.time_window,
            order_qty=1.0,  # qty 不再是策略定義的一部分,見 dsl/order_config.py
        )

        now = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
        runner.start(now, price=1000.0)  # origin_price=1000,進場目標=992.5(deviation_pct=0.75)

        runner.tick(now, 1000.0)  # sat_strategy 機制:一啟動就掛 992.5 買單
        assert runner.state == RunState.ENTRY_PENDING

        runner.tick(now + timedelta(minutes=5), 991.0)  # 碰到 992.5,成交
        assert runner.state == RunState.IN_POSITION
        assert runner.active_entry_price == 992.5

        runner.tick(now + timedelta(minutes=10), 991.0)  # 成交後馬上掛平倉 1000
        assert runner.state == RunState.EXIT_PENDING

        runner.tick(now + timedelta(minutes=15), 1000.0)  # 平倉成交
        assert runner.state == RunState.IDLE
        assert len(runner.trades) == 1
        assert runner.trades[0].entry_price == 992.5
        assert runner.trades[0].exit_price == 1000.0

        cleanup_time = runner.window_end - timedelta(minutes=1)
        runner.tick(cleanup_time, 1000.0)
        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0.0


class TestMACrossoverStrategyFromYaml:
    def test_entry_fill_exit_fill_then_cleanup(self):
        strategy = load_strategy(STRATEGIES_DIR / "ma_crossover_bracket.yaml")
        runner = StrategyRunner(
            entry=strategy.entry,
            exit=strategy.exit,
            time_window=strategy.time_window,
            order_qty=1.0,  # qty 不再是策略定義的一部分,見 dsl/order_config.py
        )

        now = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
        prices = [1000.0] * 20 + [1020.0]  # slow_window=20,最後一筆觸發交叉
        runner.start(now, prices[0])
        for i, price in enumerate(prices):
            runner.tick(now + timedelta(minutes=i), price)
        assert runner.state == RunState.ENTRY_PENDING

        runner.tick(now + timedelta(minutes=21), 1020.0)  # 貼價限價單同價成交
        assert runner.state == RunState.IN_POSITION
        assert runner.active_entry_price == 1020.0

        runner.tick(now + timedelta(minutes=22), 1031.0)  # +1.08% -> 觸發停利
        assert runner.state == RunState.EXIT_PENDING

        runner.tick(now + timedelta(minutes=23), 1031.0)
        assert runner.state == RunState.IDLE
        assert len(runner.trades) == 1
        assert runner.trades[0].entry_price == 1020.0
        assert runner.trades[0].exit_price == 1031.0
