"""區間策略(weekend_band_reversion,2026-10-10 改版)的 plugin:
上下各掛一張(買 = origin × (1 − buy_pct%)、賣 = origin × (1 + sell_pct%)),成交一張就在對面補一張。
買、賣的 % 分開設。下單與補單由 engine/band_runner.py 處理,plugin 只負責價格。"""

import pytest

from strategy_lab.registry import get as registry_get


class TestBandEntry:
    def test_prices_below_and_above_origin_set_separately(self):
        from strategy_lab.plugins.entry.band import BandEntry

        entry = BandEntry(buy_pct=0.1, sell_pct=0.2)
        buy, sell = entry.prices(100.0)
        assert buy == pytest.approx(99.9) and sell == pytest.approx(100.2)

    def test_registered_as_band_and_flags(self):
        from strategy_lab.plugins.entry.band import BandEntry

        assert registry_get("entry", "band") is BandEntry
        assert BandEntry.band is True and BandEntry.resting is True and BandEntry.scale_in is False

    @pytest.mark.parametrize("buy, sell", [(0, 0.1), (0.1, 0), (-1, 0.1), (100, 0.1), (0.1, "x")])
    def test_invalid_pct_rejected(self, buy, sell):
        from strategy_lab.plugins.entry.band import BandEntry

        with pytest.raises(ValueError, match="pct"):
            BandEntry(buy_pct=buy, sell_pct=sell)

    def test_entry_price_is_not_used(self):
        from strategy_lab.plugins.entry.band import BandEntry

        with pytest.raises(NotImplementedError):
            BandEntry(buy_pct=0.1, sell_pct=0.1).entry_price(None)


class TestBandExit:
    """策略 YAML 一定要有 exit;區間策略的平倉就是對面那張補單,exit plugin 只是標記,沒有參數。"""

    def test_registered_as_band_and_flags(self):
        from strategy_lab.plugins.exit.band import BandExit

        assert registry_get("exit", "band") is BandExit
        assert BandExit.band is True and BandExit.resting is True and BandExit.scale_in is False

    def test_exit_price_is_not_used(self):
        from strategy_lab.plugins.exit.band import BandExit

        with pytest.raises(NotImplementedError):
            BandExit().exit_price(None)
