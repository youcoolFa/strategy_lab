"""
Phase 1 demo: weekend mean-reversion strategy composed purely in Python —
no rule engine, no DSL yet, just plugin objects wired directly into the
runner. Trades against the in-memory PaperBroker + SyntheticFeed only.

Run: /opt/anaconda3/bin/python3 -m demo.demo_weekend_phase1
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strategy_lab.broker.synthetic_feed import SyntheticFeed
from strategy_lab.engine.runner import StrategyRunner
from strategy_lab.plugins.entry.deviation_from_reference import DeviationFromReferenceEntry
from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

HKT = ZoneInfo("Asia/Hong_Kong")


def main() -> None:
    runner = StrategyRunner(
        entry=DeviationFromReferenceEntry(deviation_pct=0.75),
        exit=ReturnToReferenceExit(),
        time_window=WeeklyWindow(),
        order_qty=1.0,
    )

    feed = SyntheticFeed(start_price=60000.0, volatility_pct=0.3, seed=42)
    start = datetime(2026, 8, 1, 4, 0, tzinfo=HKT)  # a Saturday
    runner.run(now=start, feed=feed, tick_interval=timedelta(minutes=5))

    print(f"origin_price = {runner.origin_price:.2f}")
    print(f"trades filled: {len(runner.trades)}")
    for i, trade in enumerate(runner.trades, 1):
        pnl = (trade.exit_price - trade.entry_price) * trade.qty
        print(f"  #{i}: entry={trade.entry_price:.2f} exit={trade.exit_price:.2f} qty={trade.qty:.4f} pnl={pnl:.2f}")
    print(f"final state: {runner.state.name}")
    print(f"position left over after cleanup: {runner.broker.position_qty():.4f}")


if __name__ == "__main__":
    main()
