"""每個示範策略各一個完整流程的整合測試:下單進場 → 成交 → 下單出場 →
成交 → 回到 idle → 窗口結束時清理。用手動挑選的確定性價格序列(不是
SyntheticFeed),所以測試不會被任何隨機數行為綁住。"""

from datetime import datetime, timedelta, timezone

from strategy_lab.engine.runner import RunState, StrategyRunner
from strategy_lab.plugins.entry.deviation_from_reference import DeviationFromReferenceEntry
from strategy_lab.plugins.entry.ma_crossover import MACrossoverEntry
from strategy_lab.plugins.exit.bracket_tp_sl import BracketTPSLExit
from strategy_lab.plugins.exit.return_to_reference import ReturnToReferenceExit
from strategy_lab.plugins.kill_switch.sustained_breakout import SustainedBreakoutKillSwitch
from strategy_lab.plugins.time_window.daily_session import DailySession
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow


class TestWeekendStrategyFullCycle:
    def test_entry_fill_exit_fill_then_cleanup(self):
        runner = StrategyRunner(
            entry=DeviationFromReferenceEntry(deviation_pct=1.0),
            exit=ReturnToReferenceExit(),
            time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
            order_qty=1.0,
        )
        now = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
        runner.start(now, price=1000.0)  # origin_price=1000,進場目標=990

        # Phase 2 起,PriceBelowReference 是真的每 tick 檢查 ctx.price,
        # 不再是「永遠 True、觸發交給掛單價」——所以價格還沒跌破目標時
        # 不會下單,跟 Phase 1 的行為不同。
        runner.tick(now, 1000.0)  # 價格==origin,還沒跌破 990
        assert runner.state == RunState.IDLE

        runner.tick(now + timedelta(minutes=5), 995.0)  # 仍未跌破 990
        assert runner.state == RunState.IDLE

        runner.tick(now + timedelta(minutes=10), 989.0)  # 跌破 990,條件成立 -> 下單
        assert runner.state == RunState.ENTRY_PENDING

        runner.tick(now + timedelta(minutes=15), 989.0)  # 限價單成交
        assert runner.state == RunState.IN_POSITION
        assert runner.active_entry_price == 990.0

        runner.tick(now + timedelta(minutes=20), 995.0)  # 還沒回到 origin=1000
        assert runner.state == RunState.IN_POSITION

        runner.tick(now + timedelta(minutes=25), 1000.0)  # 回到 origin,條件成立 -> 下單
        assert runner.state == RunState.EXIT_PENDING

        runner.tick(now + timedelta(minutes=30), 1000.0)  # 出場單成交
        assert runner.state == RunState.IDLE
        assert len(runner.trades) == 1
        assert runner.trades[0].entry_price == 990.0
        assert runner.trades[0].exit_price == 1000.0

        cleanup_time = runner.window_end - timedelta(minutes=1)
        runner.tick(cleanup_time, 1000.0)
        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0.0


class TestCrossoverStrategyFullCycle:
    def test_entry_fill_exit_fill_then_cleanup(self):
        runner = StrategyRunner(
            entry=MACrossoverEntry(fast_window=2, slow_window=4),
            exit=BracketTPSLExit(take_profit_pct=1.0, stop_loss_pct=0.5),
            time_window=DailySession(start_time="09:00", end_time="17:00", cleanup_buffer_minutes=2),
            order_qty=1.0,
        )
        now = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)

        prices = [1000.0, 1000.0, 1000.0, 1000.0, 1020.0]  # 最後一筆觸發交叉
        runner.start(now, prices[0])
        for i, price in enumerate(prices):
            runner.tick(now + timedelta(minutes=i), price)
        assert runner.state == RunState.ENTRY_PENDING

        runner.tick(now + timedelta(minutes=5), 1020.0)  # 貼價限價單同價成交
        assert runner.state == RunState.IN_POSITION
        assert runner.active_entry_price == 1020.0

        runner.tick(now + timedelta(minutes=6), 1031.0)  # +1.08% -> 觸發停利
        assert runner.state == RunState.EXIT_PENDING

        runner.tick(now + timedelta(minutes=7), 1031.0)
        assert runner.state == RunState.IDLE
        assert len(runner.trades) == 1
        assert runner.trades[0].entry_price == 1020.0
        assert runner.trades[0].exit_price == 1031.0

        cleanup_time = runner.window_end - timedelta(minutes=1)
        runner.tick(cleanup_time, 1031.0)
        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0.0


class TestKillSwitchInterruptsMidPosition:
    """kill_switch 觸發時該做的事,跟 time_window 到期時完全一樣
    (取消未成交單 + 強制平倉 + STOPPED)——差別只在觸發原因,這裡驗證
    的重點是:kill_switch 可以在 IN_POSITION(還沒等到正常出場訊號)
    的當下就直接打斷迴圈、強制平倉,不是只能在 IDLE 時生效。"""

    def test_forced_close_while_in_position_produces_no_trade_record(self):
        runner = StrategyRunner(
            entry=DeviationFromReferenceEntry(deviation_pct=1.0),
            exit=ReturnToReferenceExit(),
            time_window=WeeklyWindow(end_weekday=5, end_time="06:00", cleanup_buffer_minutes=5),
            order_qty=1.0,
            kill_switch=SustainedBreakoutKillSwitch(
                threshold_price=985.0, reference_price=900.0, days=3, margin_pct=5.0
            ),
        )
        now = datetime(2026, 8, 2, 4, 0, tzinfo=timezone.utc)  # 星期日,window_end 遠在 6 天後
        runner.start(now, price=1000.0)

        runner.tick(now, 1000.0)
        assert runner.state == RunState.IDLE

        runner.tick(now + timedelta(minutes=5), 989.0)  # 跌破 990 -> 下單
        assert runner.state == RunState.ENTRY_PENDING

        runner.tick(now + timedelta(minutes=10), 989.0)  # 進場成交
        assert runner.state == RunState.IN_POSITION
        assert runner.broker.position_qty() == 1.0

        # 接下來價格持平在 989(高於 kill switch 門檻 985,低於出場目標
        # 1000,所以正常出場訊號不會觸發),每跨一天讓前一天收盤結算一次。
        runner.tick(now + timedelta(days=1), 989.0)
        assert runner.state == RunState.IN_POSITION  # 只結算了 1 天收盤,還不夠 3 天

        runner.tick(now + timedelta(days=2), 989.0)
        assert runner.state == RunState.IN_POSITION  # 結算了 2 天,還不夠

        runner.tick(now + timedelta(days=3), 989.0)  # 結算滿 3 天,kill switch 觸發
        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0.0  # 強制平倉
        assert runner.trades == []  # 不是走正常出場流程,不會產生 Trade 紀錄


class TestRequestStopInterruptsMidPosition:
    """對應 sat_strategy/app/bot.py 的 _stop_requested——收到外部訊號
    (SIGINT/SIGTERM)時,要能跟 time_window/kill_switch 一樣觸發
    _cleanup(),不能讓真實執行入口收到 Ctrl+C 時直接把 process 砍掉、
    留下沒人管的真實掛單或部位。"""

    def test_request_stop_triggers_cleanup_even_mid_position(self):
        runner = StrategyRunner(
            entry=DeviationFromReferenceEntry(deviation_pct=1.0),
            exit=ReturnToReferenceExit(),
            time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
            order_qty=1.0,
        )
        now = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
        runner.start(now, price=1000.0)

        runner.tick(now, 1000.0)
        runner.tick(now + timedelta(minutes=5), 989.0)
        assert runner.state == RunState.ENTRY_PENDING
        runner.tick(now + timedelta(minutes=10), 989.0)
        assert runner.state == RunState.IN_POSITION
        assert runner.broker.position_qty() == 1.0

        runner.request_stop()
        runner.tick(now + timedelta(minutes=15), 989.0)  # 還沒等到正常出場訊號

        assert runner.state == RunState.STOPPED
        assert runner.broker.position_qty() == 0.0  # 強制平倉

    def test_request_stop_before_start_does_not_crash_first_tick(self):
        """呼叫順序不該有隱藏的相依性——在 start() 之前就 request_stop()
        (例如啟動腳本一收到訊號就呼叫)不應該讓第一次 tick() 出錯。"""
        runner = StrategyRunner(
            entry=DeviationFromReferenceEntry(deviation_pct=1.0),
            exit=ReturnToReferenceExit(),
            time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
            order_qty=1.0,
        )
        runner.request_stop()
        now = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
        runner.start(now, price=1000.0)

        runner.tick(now, 1000.0)

        assert runner.state == RunState.STOPPED
