"""週末均值回歸策略的時間窗:單一週期的每週場次,預設 HKT 週六 04:00 到
週一 06:00。移植自 sat_strategy/app/bot.py 的
_window_end()/_should_stop_for_cleanup()(拿掉了它裡面只給測試用的
環境變數 hook——那些是 sat_strategy 專案自己的開發便利設計,不屬於
策略邏輯本身)。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from strategy_lab.registry import register


@register("time_window", "weekly_window")
@dataclass
class WeeklyWindow:
    start_weekday: int = 5  # 星期一=0 ... 星期六=5
    start_time: str = "04:00"
    end_weekday: int = 0  # 星期一
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
        raise RuntimeError("在 8 天內找不到窗口結束時間 - 請檢查 weekday/time 設定")

    def should_cleanup(self, now: datetime, window_end: datetime) -> bool:
        return now >= window_end - timedelta(minutes=self.cleanup_buffer_minutes)

    def max_span(self) -> timedelta:
        """假設策略確實在 start_weekday/start_time 當下啟動,回傳窗口的
        跨度。`window_end()` 本身其實不看 start_weekday/start_time
        (它只從呼叫當下的 `now` 找下一個 end_weekday/end_time),所以這個
        跨度是「照文件說明的用法」算出來的預期值,不是程式碼結構上強制
        的上限——用來在 kill_switch 的視窗長度跟 time_window 明顯不相容
        時提早報錯(見 docs/ARCHITECTURE.md §4.6.1)。"""
        start_h, start_m = map(int, self.start_time.split(":"))
        monday = datetime(2024, 1, 1)  # 任選一個星期一當基準,只取相對天數差
        start_dt = (monday + timedelta(days=(self.start_weekday - monday.weekday()) % 7)).replace(
            hour=start_h, minute=start_m, second=0, microsecond=0
        )
        return self.window_end(start_dt) - start_dt
