"""持倉計時(時間暴露,2026-10-09)。

- 每一注從「建倉成交」到「平倉成交」的時間;收尾時還拿著、被市價平掉的注也算(forced)。
- 策略 YAML 的 expected_hold(預估持倉時間):`expected_hold: {value: 4, unit: hours}`,
  持倉超過就發一次 WARNING(🟡 Telegram),**不自動平倉**;沒寫 = 不預估、不警告。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, List, Optional

_UNITS = {"minutes": "minutes", "hours": "hours", "days": "days"}


@dataclass
class LotHold:
    index: int  # 第幾注
    event_index: int  # 第幾個 loop
    seconds: float  # 建倉成交 → 平倉成交
    forced: bool  # 收尾時市價強制平倉


def format_hold(delta: timedelta) -> str:
    minutes = max(int(delta.total_seconds() // 60), 0)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} 天 {hours} 小時 {mins} 分"
    if hours:
        return f"{hours} 小時 {mins} 分"
    return f"{mins} 分"


def parse_expected_hold(spec: Any) -> Optional[timedelta]:
    """`{value: 4, unit: hours}` → timedelta;None = 不預估。unit:minutes / hours / days。"""
    if spec is None:
        return None
    if not isinstance(spec, dict) or "value" not in spec or "unit" not in spec:
        raise ValueError(f"expected_hold 要寫成 {{value: 4, unit: hours}},收到 {spec!r}")
    unit = _UNITS.get(str(spec["unit"]))
    if unit is None:
        raise ValueError(f"expected_hold 的 unit 只能是 minutes / hours / days,收到 {spec['unit']!r}")
    try:
        value = float(spec["value"])
    except (TypeError, ValueError):
        raise ValueError(f"expected_hold 的 value 要是數字,收到 {spec['value']!r}") from None
    if value <= 0:
        raise ValueError(f"expected_hold 的 value 要大於 0,收到 {value:g}")
    return timedelta(**{unit: value})


def hold_summary(holds: List[LotHold], expected: Optional[timedelta]) -> Optional[str]:
    """結束總結的一行:平均 / 最長持倉、超過預估幾注、收尾強制平倉幾注。"""
    if not holds:
        return None
    avg = timedelta(seconds=sum(h.seconds for h in holds) / len(holds))
    longest = max(holds, key=lambda h: h.seconds)
    parts = [f"持倉時間:平均 {format_hold(avg)}、最長 {format_hold(timedelta(seconds=longest.seconds))}(第{longest.index}注)"]
    if expected is not None:
        over = sum(1 for h in holds if h.seconds > expected.total_seconds())
        parts.append(f"超過預估 {format_hold(expected)}:{over} 注")
    forced = sum(1 for h in holds if h.forced)
    if forced:
        parts.append(f"收尾強制平倉 {forced} 注")
    return "|".join(parts)
