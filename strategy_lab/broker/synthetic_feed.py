"""可設定亂數種子的隨機漫步價格產生器。不是真實行情資料。"""

from __future__ import annotations

import random
from typing import Iterator, Optional


class SyntheticFeed:
    def __init__(
        self,
        start_price: float,
        volatility_pct: float = 0.05,
        seed: Optional[int] = None,
        trend_pct: float = 0.0,
    ) -> None:
        """
        volatility_pct: 每個 tick 隨機波動的最大正負百分比。
        trend_pct: 每個 tick 額外疊加的固定漂移百分比(可正可負),
          在隨機波動之上。設成 >0 可以讓 demo/測試穩定觸發向上的
          均線交叉進場,不用完全靠隨機性碰運氣。
        """
        self._rng = random.Random(seed)
        self._price = start_price
        self._volatility_pct = volatility_pct
        self._trend_pct = trend_pct

    def __iter__(self) -> Iterator[float]:
        return self

    def __next__(self) -> float:
        pct_change = self._trend_pct + self._rng.uniform(-self._volatility_pct, self._volatility_pct)
        self._price = round(self._price * (1 + pct_change / 100), 2)
        return self._price
