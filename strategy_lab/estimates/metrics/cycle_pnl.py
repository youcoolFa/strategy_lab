from __future__ import annotations

from strategy_lab.engine.runner import Trade
from strategy_lab.estimates.model import Estimate, MetricResult, Row
from strategy_lab.registry import register

SHOWN_LEVELS = 3


@register("metric", "cycle_pnl")
class CyclePnlMetric:
    """完成一輪(部位 0 → 0)賺多少,依 level 分列:level k = 這一輪成交到第 k 注,
    而且每一注都在自己的平倉價平掉。level 是累加的(level 2 = 第一注 + 第二注)。
    沒有分注的策略只有 level 1,level 2/3 顯示 null。用 runner 的 Trade.pnl,
    多空公式跟實際記帳完全同一套。"""

    title = "每輪損益(依 level)"

    def compute(self, est: Estimate) -> MetricResult:
        plan = est.plan
        levels = plan.lot_levels()
        rows = []
        if getattr(plan, "band", False):
            rows = self._band_rows(est)
        elif plan.take_profit is None:
            rows.append(Row("level_1", "level 1", "出場價事前無法決定,無法估算"))
        else:
            gross = fees = exposure = 0.0
            for level in levels:
                gross += Trade(level.entry_price, level.take_profit, level.qty, plan.direction).pnl
                fees += est.lot_entry_fee(level) + est.lot_exit_fee(level, level.take_profit, resting=plan.exit_resting)
                exposure += level.notional
                net = gross - fees
                what = (f"成交到第{level.index}注、{level.index}注都平倉" if plan.scale_in
                        else "每次穿過區間(買價 ↔ 賣價)" if getattr(plan, "band", False) else "完成一輪")
                rows.append(
                    Row(
                        f"level_{level.index}", f"level {level.index}",
                        f"{net:+.4f} USDT({what};毛利 {gross:+.4f} − 手續費 {fees:.4f},"
                        f"{net / exposure * 100:+.3f}% of 名義價值 {exposure:.2f})",
                        net, warning=net <= 0, details={"gross": gross, "fees": fees, "exposure": exposure},
                    )
                )
        for k in range(len(rows) + 1, SHOWN_LEVELS + 1):  # noqa: E501
            rows.append(Row(f"level_{k}", f"level {k}", "null(這個策略沒有分注)"))
        return MetricResult(self.title, rows)

    @staticmethod
    def _band_rows(est: Estimate):
        """區間策略(sat_strategy):level 1 = 做多一輪(買價進場 → 起點平倉)、level 2 = 做空一輪(賣價進場 → 起點平倉)。
        進場、平倉都是限價單(maker);現價已經在區間外、進場單一掛就吃單成交的那一邊,進場手續費照 taker 算。"""
        plan, market = est.plan, est.market
        qty, origin, buy, sell = plan.qty, plan.take_profit, plan.entry_price, plan.band_sell_price
        long_gross = Trade(buy, origin, qty, "long").pnl
        short_gross = Trade(sell, origin, qty, "short").pnl
        buy_rate = market.taker_fee_rate if market.price <= buy else market.maker_fee_rate
        sell_rate = market.taker_fee_rate if market.price >= sell else market.maker_fee_rate
        specs = [
            (1, "做多一輪(買價 → 起點)", long_gross, buy * qty * buy_rate + origin * qty * market.maker_fee_rate, buy * qty),
            (2, "做空一輪(賣價 → 起點)", short_gross, sell * qty * sell_rate + origin * qty * market.maker_fee_rate, sell * qty),
        ]
        rows = []
        for index, what, gross, fees, exposure in specs:
            net = gross - fees
            rows.append(Row(
                f"level_{index}", f"level {index}",
                f"{net:+.4f} USDT({what};毛利 {gross:+.4f} − 手續費 {fees:.4f},"
                f"{net / exposure * 100:+.3f}% of 名義價值 {exposure:.2f})",
                net, warning=net <= 0, details={"gross": gross, "fees": fees, "exposure": exposure},
            ))
        return rows
