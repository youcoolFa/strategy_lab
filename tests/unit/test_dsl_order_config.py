"""dsl/order_config.py:「怎麼下單」的資料(symbol/order_type/
position_sizing),跟 strategies/*.yaml(「什麼時候該不該觸發」的邏輯)
分開。sandbox 跟 live 各自一份設定檔,共用這裡的 schema 跟換算邏輯。"""

import pytest

from strategy_lab.dsl.order_config import OrderConfig, PositionSizing, compute_qty, load_order_config


class TestLoadOrderConfig:
    def test_loads_fixed_qty_mode(self, tmp_path):
        path = tmp_path / "order.yaml"
        path.write_text(
            "symbol: null\n"
            "order_type: limit\n"
            "position_sizing:\n"
            "  mode: fixed_qty\n"
            "  value: 0.01\n"
        )

        config = load_order_config(path)

        assert config.symbol is None
        assert config.order_type == "limit"
        assert config.position_sizing == PositionSizing(mode="fixed_qty", value=0.01)
        assert config.account_value is None

    def test_loads_account_percentage_mode_with_account_value(self, tmp_path):
        path = tmp_path / "order.yaml"
        path.write_text(
            "symbol: BTCUSDT\n"
            "position_sizing:\n"
            "  mode: account_percentage\n"
            "  value: 2.0\n"
            "account_value: 10000.0\n"
        )

        config = load_order_config(path)

        assert config.symbol == "BTCUSDT"
        assert config.position_sizing == PositionSizing(mode="account_percentage", value=2.0)
        assert config.account_value == 10000.0

    def test_missing_fields_use_defaults(self, tmp_path):
        path = tmp_path / "order.yaml"
        path.write_text("symbol: BTCUSDT\n")

        config = load_order_config(path)

        assert config.order_type == "limit"
        assert config.position_sizing == PositionSizing(mode="fixed_qty", value=1.0)


class TestComputeQty:
    def test_fixed_qty_ignores_price(self):
        config = OrderConfig(position_sizing=PositionSizing(mode="fixed_qty", value=0.05))
        assert compute_qty(config, current_price=60000.0) == 0.05

    def test_fixed_quote_amount_divides_by_price(self):
        config = OrderConfig(position_sizing=PositionSizing(mode="fixed_quote_amount", value=500.0))
        assert compute_qty(config, current_price=50000.0) == pytest.approx(0.01)

    def test_account_percentage_uses_account_value_and_price(self):
        config = OrderConfig(
            position_sizing=PositionSizing(mode="account_percentage", value=2.0),
            account_value=10000.0,
        )
        # 10000 的 2% = 200,除以現價 50000 = 0.004
        assert compute_qty(config, current_price=50000.0) == pytest.approx(0.004)

    def test_account_percentage_without_account_value_raises(self):
        """live 端目前沒有查真實帳戶餘額的功能——沒給 account_value 就
        raise,不會靜默算出一個危險或錯誤的數字。"""
        config = OrderConfig(position_sizing=PositionSizing(mode="account_percentage", value=2.0))
        with pytest.raises(ValueError, match="account_value"):
            compute_qty(config, current_price=50000.0)

    def test_unknown_mode_raises(self):
        config = OrderConfig(position_sizing=PositionSizing(mode="not_a_real_mode", value=1.0))
        with pytest.raises(ValueError, match="not_a_real_mode"):
            compute_qty(config, current_price=50000.0)
