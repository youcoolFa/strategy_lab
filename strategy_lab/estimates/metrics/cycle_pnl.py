from __future__ import annotations

from strategy_lab.engine.runner import Trade
from strategy_lab.estimates.model import Estimate, MetricResult, Row
from strategy_lab.registry import register


@register("metric", "cycle_pnl")
class CyclePnlMetric:
    """完成一輪(進場 → 平倉單成交)賺多少。用 runner 的 Trade.pnl,多空公式
    跟實際記帳完全同一套。"""

    title = "每輪損益(完成一輪才實現)"

    def compute(self, est: Estimate) -> MetricResult:
        plan = est.plan
        if plan.take_profit is None:
            return MetricResult(self.title, [Row("gross_pnl", "每輪損益", "出場價事前無法決定,無法估算")])

        gross = Trade(plan.entry_price, plan.take_profit, plan.qty, plan.direction).pnl
        fees = est.entry_fee() + est.exit_fee(plan.take_profit, resting=plan.exit_resting)
        net = gross - fees
        entry_kind = "吃單" if plan.entry_crosses_market else "掛單"
        exit_kind = "掛單" if plan.exit_resting else "吃單"
        return MetricResult(
            self.title,
            [
                Row("gross_pnl", "價差毛利", f"{gross:+.4f} USDT", gross),
                Row("fees", "手續費", f"-{fees:.4f} USDT(進場{entry_kind} + 平倉{exit_kind})", fees),
                Row("net_pnl", "淨利", f"{net:+.4f} USDT({net / plan.notional * 100:+.3f}% of 名義價值)", net, warning=net <= 0),
            ],
        )
