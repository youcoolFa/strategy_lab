"""
驗證 KillSwitch 在真正的模擬交易沙盒(SyntheticFeed + PaperBroker,不是
手動編排的確定性價格序列)裡,跑完整條 tick 迴圈也能正確觸發。

跟 unit test / runner 整合測試的差別:那兩層都是手動挑選價格序列,直接
呼叫 tick() 驗證單一次轉移;這裡是讓 SyntheticFeed 真的產生一段有趨勢的
隨機價格,自己控制 tick 迴圈(不用 runner.run(),因為要在每個 tick 都
記錄 now,才能判斷最後是被 time_window 還是 kill_switch 停下來的),
驗證整個系統疊起來之後行為跟單獨測試時預期的一致。

執行方式:/opt/anaconda3/bin/python3 -m demo.demo_kill_switch_sandbox
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strategy_lab.broker.synthetic_feed import SyntheticFeed
from strategy_lab.engine.runner import RunState, StrategyRunner
from strategy_lab.plugins.entry.deviation_from_reference import DeviationFromReferenceEntry
from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit
from strategy_lab.plugins.kill_switch.sustained_breakout import SustainedBreakoutKillSwitch
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

HKT = ZoneInfo("Asia/Hong_Kong")


def main() -> None:
    runner = StrategyRunner(
        entry=DeviationFromReferenceEntry(deviation_pct=1.0),
        exit=ReturnToReferenceExit(),
        # window_end 故意設得很遠(接近一週後),這樣如果在那之前很久就
        # STOPPED,就能確定是 kill_switch 觸發的,不是排程到期。
        time_window=WeeklyWindow(end_weekday=5, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=1.0,
        kill_switch=SustainedBreakoutKillSwitch(
            threshold_price=1010.0, reference_price=1000.0, hours=72.0, margin_pct=3.0
        ),
    )

    # 帶明顯上升趨勢、低雜訊,讓價格大機率連續好幾天站穩在門檻之上。
    feed = SyntheticFeed(start_price=1000.0, volatility_pct=0.1, trend_pct=0.3, seed=11)
    now = datetime(2026, 8, 2, 4, 0, tzinfo=HKT)  # 星期日
    tick_interval = timedelta(hours=6)

    price = next(feed)
    runner.start(now, price)
    runner.tick(now, price)
    max_ticks = 400
    for _ in range(max_ticks - 1):
        if runner.state == RunState.STOPPED:
            break
        now += tick_interval
        price = next(feed)
        runner.tick(now, price)

    print(f"window_end          = {runner.window_end}")
    print(f"stopped at          = {now}")
    print(f"final state         = {runner.state.name}")
    print(f"trades filled       = {len(runner.trades)}")
    for i, trade in enumerate(runner.trades, 1):
        print(f"  #{i}: entry={trade.entry_price:.2f} exit={trade.exit_price:.2f}")
    print(f"position after stop = {runner.broker.position_qty():.4f}")

    gap_to_window_end = runner.window_end - now
    print(f"距離 window_end 還有 {gap_to_window_end} —— 遠大於 0 代表是 kill_switch 提前觸發,不是排程到期")


if __name__ == "__main__":
    main()
