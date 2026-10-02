from __future__ import annotations

from typing import List, Optional

from strategy_lab.estimates.metrics import cycle_pnl, order_plan, risk, time_window  # noqa: F401  (觸發 @register)
from strategy_lab.estimates.model import Estimate, MetricResult
from strategy_lab.registry import get

DEFAULT_METRICS = ["order_plan", "cycle_pnl", "risk", "time_window"]


def run_metrics(estimate: Estimate, names: Optional[List[str]] = None) -> List[MetricResult]:
    return [get("metric", name)().compute(estimate) for name in (names or DEFAULT_METRICS)]
