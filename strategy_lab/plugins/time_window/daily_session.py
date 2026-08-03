"""MA-crossover strategy's time window: a daily intraday session, e.g.
HKT 09:00-17:00 every day. A genuinely different recurrence rule from
WeeklyWindow's single-weekday session, not just different constants."""

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
