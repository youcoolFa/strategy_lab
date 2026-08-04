"""
Phase 1 demo:MA 均線交叉 + 止盈止損括號單策略——跟
demo_weekend_phase1.py 用同一個 runner、同一套 PaperBroker/SyntheticFeed,
但進場/出場/時間窗的 plugin 物件完全不同,runner 裡也沒有任何共用的
分支邏輯。

執行方式:/opt/anaconda3/bin/python3 -m demo.demo_crossover_phase1
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

    # 帶一點小幅上升趨勢,讓快線在盤中場次內能穩定穿越慢線,不用完全
    # 靠隨機性碰運氣。
    feed = SyntheticFeed(start_price=60000.0, volatility_pct=0.15, trend_pct=0.03, seed=7)
    start = datetime(2026, 8, 3, 9, 0, tzinfo=HKT)  # 一個星期一
    runner.run(now=start, feed=feed, tick_interval=timedelta(minutes=1))

    print(f"trades filled: {len(runner.trades)}")
    for i, trade in enumerate(runner.trades, 1):
        pnl = (trade.exit_price - trade.entry_price) * trade.qty
        print(f"  #{i}: entry={trade.entry_price:.2f} exit={trade.exit_price:.2f} qty={trade.qty:.4f} pnl={pnl:.2f}")
    print(f"final state: {runner.state.name}")
    print(f"position left over after cleanup: {runner.broker.position_qty():.4f}")


if __name__ == "__main__":
    main()
