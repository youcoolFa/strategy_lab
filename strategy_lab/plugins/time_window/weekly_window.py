"""Weekend mean-reversion's time window: a single weekly session, default
HKT Sat 04:00 -> Mon 06:00. Ported from sat_strategy/app/bot.py's
_window_end()/_should_stop_for_cleanup() (minus its test-only env-var
hooks, which are sat_strategy-specific dev conveniences, not part of the
strategy's logic)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from strategy_lab.registry import register


@register("time_window", "weekly_window")
@dataclass
class WeeklyWindow:
    start_weekday: int = 5  # Monday=0 ... Saturday=5
    start_time: str = "04:00"
    end_weekday: int = 0  # Monday
    end_time: str = "06:00"
    cleanup_buffer_minutes: int = 5

    def window_end(self, now: datetime) -> datetime:
        end_h, end_m = map(int, self.end_time.split(":"))
        candidate = now
        for _ in range(8):
            if candidate.weekday() == self.end_weekday:
                end_dt = candidate.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
                if end_dt >= now:
                    return end_dt
            candidate += timedelta(days=1)
        raise RuntimeError("could not find a window end within 8 days - check weekday/time config")

    def should_cleanup(self, now: datetime, window_end: datetime) -> bool:
        return now >= window_end - timedelta(minutes=self.cleanup_buffer_minutes)
