"""
Phase 1 demo: MA-crossover-with-bracket-TP/SL strategy — same runner, same
PaperBroker/SyntheticFeed as demo_weekend_phase1.py, completely different
entry/exit/time-window plugin objects and zero shared branching logic in
the runner itself.

Run: /opt/anaconda3/bin/python3 -m demo.demo_crossover_phase1
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strategy_lab.broker.synthetic_feed import SyntheticFeed
from strategy_lab.engine.runner import StrategyRunner
from strategy_lab.plugins.entry.ma_crossover import MACrossoverEntry
from strategy_lab.plugins.exit.bracket_tp_sl import BracketTPSLExit
from strategy_lab.plugins.time_window.daily_session import DailySession

HKT = ZoneInfo("Asia/Hong_Kong")


def main() -> None:
    runner = StrategyRunner(
        entry=MACrossoverEntry(fast_window=5, slow_window=20),
        exit=BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5),
        time_window=DailySession(start_time="09:00", end_time="17:00"),
        order_qty=1.0,
    )

    # Slight upward trend so the fast SMA reliably crosses above the slow
    # SMA within the session instead of depending purely on randomness.
    feed = SyntheticFeed(start_price=60000.0, volatility_pct=0.15, trend_pct=0.03, seed=7)
    start = datetime(2026, 8, 3, 9, 0, tzinfo=HKT)  # a Monday
    runner.run(now=start, feed=feed, tick_interval=timedelta(minutes=1))

    print(f"trades filled: {len(runner.trades)}")
    for i, trade in enumerate(runner.trades, 1):
        pnl = (trade.exit_price - trade.entry_price) * trade.qty
        print(f"  #{i}: entry={trade.entry_price:.2f} exit={trade.exit_price:.2f} qty={trade.qty:.4f} pnl={pnl:.2f}")
    print(f"final state: {runner.state.name}")
    print(f"position left over after cleanup: {runner.broker.position_qty():.4f}")


if __name__ == "__main__":
    main()
