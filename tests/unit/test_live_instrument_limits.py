"""live/instrument_limits.py:Bybit 每個交易對的價格/數量精度限制
(priceFilter.tickSize、lotSizeFilter.qtyStep 等)——不照這個精度下單,
交易所會直接拒單。這裡測的是純計算邏輯(修正 qty/price 到合法精度),
完全不碰網路、不碰檔案(load_instrument_limits 的檔案讀取另外測)。"""

import json

import pytest

from strategy_lab.live.instrument_limits import (
    InstrumentLimits,
    UnknownSymbolError,
    check_min_notional,
    fix_price,
    fix_qty,
    load_instrument_limits,
)

BTCUSDT_LIMITS = InstrumentLimits(
    qty_step=0.001,
    min_qty=0.001,
    max_qty=1500.0,
    max_market_qty=150.0,
    tick_size=0.10,
    min_price=0.10,
    max_price=1999999.80,
    min_notional=5.0,
)


class TestFixQty:
    def test_rounds_down_to_nearest_step_not_up(self):
        """浮點數/position_sizing 算出來的 qty(例如 0.003333...)常常不是
        qty_step 的整數倍——這是使用者提出這個功能時具體指出的「數值
        浮點數問題」,永遠只捨去,不會偷偷幫使用者多下一點。"""
        assert fix_qty(0.003333, BTCUSDT_LIMITS) == pytest.approx(0.003)

    def test_already_valid_qty_is_unchanged(self):
        assert fix_qty(0.010, BTCUSDT_LIMITS) == pytest.approx(0.010)

    def test_market_order_uses_the_stricter_max_market_qty(self):
        """maxMktOrderQty(市價單專用上限)通常比一般 maxOrderQty 小,
        兩者是 Bybit 真實回傳資料裡兩個不同的欄位,不能共用同一個上限。"""
        qty = fix_qty(200.0, BTCUSDT_LIMITS, order_type="market")
        assert qty == BTCUSDT_LIMITS.max_market_qty

    def test_limit_order_uses_the_looser_max_qty(self):
        qty = fix_qty(2000.0, BTCUSDT_LIMITS, order_type="limit")
        assert qty == BTCUSDT_LIMITS.max_qty

    def test_qty_below_minimum_after_rounding_raises_not_silently_bumped(self):
        """捨去到 qty_step 之後如果小於 min_qty,不會偷偷幫使用者調整到
        最小值——那樣會悄悄改變 position_sizing 原本算好的風險大小,
        寧可直接報錯讓使用者知道。"""
        with pytest.raises(ValueError, match="min_qty|最小"):
            fix_qty(0.0001, BTCUSDT_LIMITS)


class TestFixPrice:
    def test_buy_price_rounds_down_never_overpays(self):
        assert fix_price(60000.07, BTCUSDT_LIMITS, side="Buy") == pytest.approx(60000.00)

    def test_sell_price_rounds_up_never_undersells(self):
        assert fix_price(60000.03, BTCUSDT_LIMITS, side="Sell") == pytest.approx(60000.10)

    def test_price_clamped_within_min_and_max(self):
        assert fix_price(0.01, BTCUSDT_LIMITS, side="Buy") == BTCUSDT_LIMITS.min_price


class TestCheckMinNotional:
    def test_passes_when_above_minimum(self):
        check_min_notional(qty=0.01, price=60000.0, limits=BTCUSDT_LIMITS)  # 不應該 raise

    def test_raises_when_below_minimum(self):
        with pytest.raises(ValueError, match="min_notional|最小"):
            check_min_notional(qty=0.00001, price=60000.0, limits=BTCUSDT_LIMITS)


class TestLoadInstrumentLimits:
    def test_loads_and_parses_real_shaped_response(self, tmp_path):
        path = tmp_path / "instrument_limits.json"
        path.write_text(
            json.dumps(
                {
                    "BTCUSDT": {
                        "priceFilter": {"minPrice": "0.10", "maxPrice": "1999999.80", "tickSize": "0.10"},
                        "lotSizeFilter": {
                            "maxOrderQty": "1500.000",
                            "minOrderQty": "0.001",
                            "qtyStep": "0.001",
                            "maxMktOrderQty": "150.000",
                            "minNotionalValue": "5",
                        },
                    }
                }
            )
        )

        limits = load_instrument_limits("BTCUSDT", path=path)

        assert limits == BTCUSDT_LIMITS

    def test_unknown_symbol_raises_clear_error(self, tmp_path):
        path = tmp_path / "instrument_limits.json"
        path.write_text(json.dumps({"BTCUSDT": {}}))

        with pytest.raises(UnknownSymbolError, match="ETHUSDT"):
            load_instrument_limits("ETHUSDT", path=path)
