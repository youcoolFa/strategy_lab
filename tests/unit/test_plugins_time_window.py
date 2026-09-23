from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strategy_lab.plugins.time_window.daily_session import DailySession
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

HKT = ZoneInfo("Asia/Hong_Kong")


class TestWeeklyWindow:
    def test_window_end_finds_next_matching_weekday(self):
        window = WeeklyWindow(end_weekday=0, end_time="06:00")  # 星期一 06:00
        now = datetime(2026, 8, 1, 4, 0, tzinfo=HKT)  # 星期六
        assert window.window_end(now) == datetime(2026, 8, 3, 6, 0, tzinfo=HKT)

    def test_window_end_same_day_if_still_ahead(self):
        window = WeeklyWindow(end_weekday=0, end_time="06:00")
        now = datetime(2026, 8, 3, 5, 0, tzinfo=HKT)  # 星期一,還沒到 06:00
        assert window.window_end(now) == datetime(2026, 8, 3, 6, 0, tzinfo=HKT)

    def test_should_cleanup_within_buffer(self):
        window = WeeklyWindow(cleanup_buffer_minutes=5)
        end = datetime(2026, 8, 3, 6, 0, tzinfo=HKT)
        assert window.should_cleanup(datetime(2026, 8, 3, 5, 56, tzinfo=HKT), end) is True
        assert window.should_cleanup(datetime(2026, 8, 3, 5, 54, tzinfo=HKT), end) is False

    def test_max_span_matches_start_weekday_time_to_end_weekday_time(self):
        # 預設:start_weekday=5(週六)04:00 -> end_weekday=0(週一)06:00
        # = 2 天 2 小時 = 50 小時,假設策略確實是在 start_weekday/start_time
        # 當下啟動(見 docs/ARCHITECTURE.md §4.6.1)。
        window = WeeklyWindow()
        assert window.max_span() == timedelta(days=2, hours=2)

    def test_max_span_changes_with_start_end_weekday_gap(self):
        window = WeeklyWindow(start_weekday=0, start_time="00:00", end_weekday=1, end_time="00:00")
        assert window.max_span() == timedelta(days=1)


class TestDailySession:
    def test_window_end_same_day(self):
        window = DailySession(end_time="17:00")
        now = datetime(2026, 8, 3, 9, 0, tzinfo=HKT)
        assert window.window_end(now) == datetime(2026, 8, 3, 17, 0, tzinfo=HKT)

    def test_window_end_rolls_to_next_day_if_already_past(self):
        window = DailySession(end_time="17:00")
        now = datetime(2026, 8, 3, 18, 0, tzinfo=HKT)
        assert window.window_end(now) == datetime(2026, 8, 4, 17, 0, tzinfo=HKT)

    def test_should_cleanup_within_buffer(self):
        window = DailySession(cleanup_buffer_minutes=2)
        end = datetime(2026, 8, 3, 17, 0, tzinfo=HKT)
        assert window.should_cleanup(datetime(2026, 8, 3, 16, 59, tzinfo=HKT), end) is True
        assert window.should_cleanup(datetime(2026, 8, 3, 16, 57, 30, tzinfo=HKT), end) is False

    def test_max_span_matches_configured_session_length(self):
        window = DailySession(start_time="09:00", end_time="17:00")
        assert window.max_span() == timedelta(hours=8)
