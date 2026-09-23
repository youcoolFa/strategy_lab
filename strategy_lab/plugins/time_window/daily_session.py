"""MA 均線交叉策略的時間窗:每天都有的盤中場次,例如 HKT 09:00-17:00。
跟 WeeklyWindow 的單一星期場次比,是真正不同的週期規則,不只是換了
幾個常數而已。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from strategy_lab.registry import register


@register("time_window", "daily_session")
@dataclass
class DailySession:
    start_time: str = "09:00"
    end_time: str = "17:00"
    cleanup_buffer_minutes: int = 2

    def window_end(self, now: datetime) -> datetime:
        end_h, end_m = map(int, self.end_time.split(":"))
        end_dt = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        if end_dt < now:
            end_dt += timedelta(days=1)
        return end_dt

    def should_cleanup(self, now: datetime, window_end: datetime) -> bool:
        return now >= window_end - timedelta(minutes=self.cleanup_buffer_minutes)

    def max_span(self) -> timedelta:
        """假設策略確實在 start_time 當下啟動,回傳場次的跨度(見
        WeeklyWindow.max_span() 的說明,道理一樣)。"""
        start_h, start_m = map(int, self.start_time.split(":"))
        start_dt = datetime(2024, 1, 1, start_h, start_m)
        return self.window_end(start_dt) - start_dt
