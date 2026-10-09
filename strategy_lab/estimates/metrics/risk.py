from __future__ import annotations

from strategy_lab.engine.runner import Trade
from strategy_lab.estimates.model import Estimate, MetricResult, Row
from strategy_lab.registry import register

ADVERSE_MOVES_PCT = (1, 3, 5, 10)


@register("metric", "risk")
class RiskMetric:
    """有停損(出場 plugin 回報 stop_loss)→ 最大虧損有上限;沒有 → 標示沒有
    上限,列出價格往不利方向走的情境。最大回撤(max drawdown)要用實際交易
    紀錄的權益曲線算,啟動前只能給情境。"""

    title = "風險"

    def compute(self, est: Estimate) -> MetricResult:
        plan, market = est.plan, est.market
        long = plan.direction != "short"
        notional = plan.notional
        base = plan.avg_entry_price
        basis = f"(全部 {len(plan.levels)} 注成交、平均建倉價 {base:.2f} 為基準)" if plan.scale_in else ""
        rows = [
            Row("effective_leverage", "實際槓桿", f"{notional / market.equity:.2f} 倍(名義價值 {notional:.2f} ÷ 權益 {market.equity:.2f}){basis}", notional / market.equity),
            Row("account_settings", "帳戶設定", f"槓桿 {market.leverage} 倍,保證金模式 {market.margin_mode}"),
        ]

        if plan.stop_loss is not None:
            loss = self._loss_at(est, plan.stop_loss)
            rows.append(Row("max_loss", "最大虧損", f"{loss:+.4f} USDT(停損 {plan.stop_loss},權益 {loss / market.equity * 100:+.2f}%)", loss))
        else:
            rows.append(
                Row(
                    "max_loss", "最大虧損",
                    f"無停損:持倉到收尾前虧損沒有上限,收尾時以市價平倉(見下面情境)",
                    None, warning=True,
                )
            )

        if getattr(plan, "band", False):
            rows.append(Row("band_side", "持倉方向",
                            f"區間策略:第一張成交後持倉一直是多單或空單(±{plan.qty:g}),價格往上或往下突破區間都會虧;"
                            "下面以多單、價格往下為例,空單往上的虧損量級相同"))

        for pct in ADVERSE_MOVES_PCT:
            price = base * (1 - pct / 100 if long else 1 + pct / 100)
            loss = self._loss_at(est, price)
            rows.append(
                Row(f"scenario_{pct}pct", f"不利 {pct}%", f"{loss:+.4f} USDT(價格 {price:.2f},權益 {loss / market.equity * 100:+.2f}%)", loss)
            )

        wipeout = market.equity / notional * 100
        wipeout_price = base * (1 - wipeout / 100 if long else 1 + wipeout / 100)
        rows.append(
            Row(
                "equity_wipeout_move", "權益虧光",
                f"價格不利 {wipeout:.1f}%(約 {wipeout_price:.2f});實際爆倉會更早一點(維持保證金)",
                wipeout,
            )
        )
        return MetricResult(self.title, rows)

    @staticmethod
    def _loss_at(est: Estimate, exit_price: float) -> float:
        plan = est.plan
        pnl = sum(Trade(l.entry_price, exit_price, l.qty, plan.direction).pnl for l in plan.lot_levels())
        return pnl - est.entry_fee() - est.exit_fee(exit_price, resting=False)
