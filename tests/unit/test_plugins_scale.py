"""分注策略的兩個 plugin:建倉比重(數量)與平倉距離(% 或點數)。
建倉價由使用者在 live_execution_config.yaml 的 entry_prices 輸入,不在這裡。"""

import pytest

from strategy_lab.plugins.entry.scale_in import ScaleInEntry
from strategy_lab.plugins.exit.scale_out import ScaleOutExit
from strategy_lab.registry import get


class TestScaleInEntry:
    def test_weights_only_decide_quantity_relative_to_first_lot(self):
        entry = ScaleInEntry(weights=[2, 3, 1])
        assert entry.lot_qtys(2.0) == pytest.approx([2.0, 3.0, 1.0])
        assert entry.lot_qtys(0.002) == pytest.approx([0.002, 0.003, 0.001])
        assert entry.lots == 3

    @pytest.mark.parametrize("weights", [[], [2, 0, 1], [2, -1]])
    def test_rejects_empty_or_non_positive_weights(self, weights):
        with pytest.raises(ValueError, match="weights"):
            ScaleInEntry(weights=weights)

    def test_flags_and_registry(self):
        assert ScaleInEntry.scale_in is True and ScaleInEntry.resting is True
        assert get("entry", "scale_in") is ScaleInEntry


class TestScaleOutExit:
    def test_pct_distance_long_and_short(self):
        exit = ScaleOutExit(distance={"value": 1.0, "unit": "pct"})
        assert exit.exit_price_for(1000.0, "long") == pytest.approx(1010.0)
        assert exit.exit_price_for(1000.0, "short") == pytest.approx(990.0)

    def test_points_distance_long_and_short(self):
        exit = ScaleOutExit(distance={"value": 500, "unit": "points"})
        assert exit.exit_price_for(84000.0, "long") == pytest.approx(84500.0)
        assert exit.exit_price_for(84000.0, "short") == pytest.approx(83500.0)

    @pytest.mark.parametrize("distance", [{"value": 1, "unit": "bps"}, {"value": 0, "unit": "pct"}, {"unit": "pct"}])
    def test_rejects_bad_distance(self, distance):
        with pytest.raises(ValueError, match="distance"):
            ScaleOutExit(distance=distance)

    def test_flags_and_registry(self):
        assert ScaleOutExit.scale_in is True and ScaleOutExit.resting is True
        assert get("exit", "scale_out") is ScaleOutExit
