from __future__ import annotations

import math
from zoneinfo import ZoneInfo

from strategy_lab.estimates.model import Estimate, MetricResult, Row
from strategy_lab.registry import register

HKT = ZoneInfo("Asia/Hong_Kong")


@register("metric", "time_window")
class TimeWindowMetric:
    title = "時間窗"

    def compute(self, est: Estimate) -> MetricResult:
        plan, market = est.plan, est.market
        remaining = plan.cleanup_at - market.now
        hours = remaining.total_seconds() / 3600
        when = plan.cleanup_at.astimezone(HKT).strftime("%Y-%m-%d %H:%M")
        if hours <= 0:
            cleanup = Row("cleanup_at", "收尾時間", f"{when} HKT——已經過了,啟動後會馬上收尾", hours, warning=True)
        else:
            cleanup = Row("cleanup_at", "收尾時間", f"{when} HKT(還有 {int(hours)} 小時 {int(hours % 1 * 60)} 分),到時取消掛單、市價平倉", hours)

        interval = market.funding_interval_minutes * 60
        settlements = max(0, math.floor(plan.cleanup_at.timestamp() / interval) - math.floor(market.now.timestamp() / interval))
        funding = Row(
            "funding_settlements", "資金費結算",
            f"窗口內 {settlements} 次(每 {market.funding_interval_minutes // 60} 小時);持倉跨過結算時間才會收付",
            settlements,
        )
        if plan.loop is None:
            loop = Row("loop", "event 次數", "不限(loop: null),一直做到收尾時間", None)
        else:
            total = plan.loop + 1
            loop = Row("loop", "event 次數", f"最多 {total} 個(loop: {plan.loop}),做完就收尾結束", total)
        return MetricResult(self.title, [cleanup, funding, loop])
