from __future__ import annotations

from strategy_lab.estimates.model import Estimate, MetricResult, Row
from strategy_lab.registry import register


def _pct(a: float, b: float) -> str:
    return f"{(a / b - 1) * 100:+.2f}%"


@register("metric", "order_plan")
class OrderPlanMetric:
    title = "掛單計畫"

    def compute(self, est: Estimate) -> MetricResult:
        plan, market = est.plan, est.market
        long = plan.direction != "short"
        entry_side, exit_side = ("買", "賣") if long else ("賣(放空)", "買回")
        rows = [
            Row("price", "現價", f"{market.price}", market.price),
            Row("origin", "origin", f"{plan.origin_price}({plan.origin_source}),離現價 {_pct(plan.origin_price, market.price)}", plan.origin_price),
        ]
        entry_note = "" if plan.entry_known_in_advance else "(依訊號進場,價格事前無法確定,以現價估算)"
        rows.append(
            Row("entry", f"進場{entry_side}", f"{plan.entry_price},離現價 {_pct(plan.entry_price, market.price)}{entry_note}", plan.entry_price)
        )
        if plan.entry_crosses_market:
            where = "之上" if long else "之下"
            rows.append(
                Row(
                    "entry_crosses_market", "⚠ 進場",
                    f"進場價在現價{where},一掛出去會立刻吃單成交(origin 可能過期,等於直接用市價進場)",
                    warning=True,
                )
            )
        else:
            rows.append(Row("entry_crosses_market", "進場", "會掛在交易所上等價格碰到(maker)"))
        if plan.take_profit is not None:
            rows.append(Row("take_profit", f"平倉{exit_side}", f"{plan.take_profit},離現價 {_pct(plan.take_profit, market.price)}", plan.take_profit))
        else:
            rows.append(Row("take_profit", "平倉", "依訊號/時間出場,沒有預定價位"))
        if plan.stop_loss is not None:
            rows.append(Row("stop_loss", "停損", f"{plan.stop_loss},離進場 {_pct(plan.stop_loss, plan.entry_price)}", plan.stop_loss))
        else:
            rows.append(Row("stop_loss", "停損", "無停損"))
        rows.append(Row("notional", "數量 / 名義價值", f"{plan.qty} / {plan.notional:.2f} USDT", plan.notional))
        return MetricResult(self.title, rows)
