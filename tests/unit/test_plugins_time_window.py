from datetime import datetime
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
