"""Seedable random-walk price generator. No real market data."""

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
        volatility_pct: max +/- percent random move per tick.
        trend_pct: constant percent drift added every tick, on top of the
          random move (set >0 to reliably exercise a crossover-up entry in
          demos/tests without waiting on pure randomness).
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
