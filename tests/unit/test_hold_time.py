"""持倉計時(engine/hold_time.py,2026-10-09):每一注從建倉成交到平倉成交的時間(時間暴露)、
策略的預估持倉時間 expected_hold、結束總結的統計。"""

from datetime import timedelta

import pytest

from strategy_lab.engine.hold_time import LotHold, format_hold, hold_summary, parse_expected_hold


class TestFormatHold:
    @pytest.mark.parametrize("delta, text", [
        (timedelta(minutes=15), "15 分"),
        (timedelta(hours=3, minutes=12), "3 小時 12 分"),
        (timedelta(days=1, hours=2, minutes=3), "1 天 2 小時 3 分"),
        (timedelta(seconds=30), "0 分"),
        (timedelta(minutes=-5), "0 分"),
    ])
    def test_formats(self, delta, text):
        assert format_hold(delta) == text


class TestParseExpectedHold:
    def test_none_means_no_estimate(self):
        assert parse_expected_hold(None) is None

    @pytest.mark.parametrize("spec, delta", [
        ({"value": 4, "unit": "hours"}, timedelta(hours=4)),
        ({"value": 90, "unit": "minutes"}, timedelta(minutes=90)),
        ({"value": 1.5, "unit": "days"}, timedelta(days=1.5)),
    ])
    def test_units(self, spec, delta):
        assert parse_expected_hold(spec) == delta

    @pytest.mark.parametrize("spec", [
        {"value": 4, "unit": "weeks"}, {"value": 0, "unit": "hours"}, {"value": -1, "unit": "hours"},
        {"unit": "hours"}, {"value": 4}, "4h",
    ])
    def test_invalid(self, spec):
        with pytest.raises(ValueError):
            parse_expected_hold(spec)


class TestHoldSummary:
    def test_nothing_closed_yet(self):
        assert hold_summary([], timedelta(hours=4)) is None

    def test_average_longest_and_over_estimate(self):
        holds = [
            LotHold(index=1, event_index=1, seconds=3600, forced=False),
            LotHold(index=2, event_index=1, seconds=5 * 3600, forced=False),
            LotHold(index=1, event_index=2, seconds=2 * 3600, forced=True),
        ]
        text = hold_summary(holds, timedelta(hours=4))
        assert "平均 2 小時 40 分" in text
        assert "最長 5 小時 0 分(第2注)" in text
        assert "超過預估 4 小時 0 分:1 注" in text
        assert "收尾強制平倉 1 注" in text

    def test_without_estimate_no_over_count(self):
        text = hold_summary([LotHold(index=1, event_index=1, seconds=600, forced=False)], None)
        assert "超過預估" not in text and "平均 10 分" in text
