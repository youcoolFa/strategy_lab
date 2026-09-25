"""interfaces.py 的 Broker/OrderLike Protocol——泛化 StrategyRunner 的
broker 欄位,讓它不再寫死 PaperBroker 型別。`PaperBroker`/`LiveBroker`
都要滿足同一套 8 種下單方式(見 docs/ARCHITECTURE.md §6.8),兩者
結構上已經滿足這個 Protocol,不需要另外繼承或註冊。"""

import inspect

from strategy_lab.broker.paper_broker import Order, PaperBroker
from strategy_lab.engine.runner import StrategyRunner
from strategy_lab.interfaces import Broker, OrderLike

_ALL_BROKER_METHODS = [
    "place_limit_buy",
    "limit_sell",
    "market_buy",
    "market_sell",
    "place_limit_sell",
    "limit_flat_buy",
    "limit_flat_sell",
    "market_flat_buy",
    "market_flat_sell",
    "fetch_order",
    "cancel_order",
    "position_qty",
    "market_close",
    "tick",
]


class TestBrokerProtocolConformance:
    def test_paper_broker_satisfies_broker_protocol_without_any_changes(self):
        assert isinstance(PaperBroker(), Broker)

    def test_paper_broker_order_satisfies_order_like_protocol(self):
        order = Order(id="1", side="buy", price=100.0, qty=1.0)
        assert isinstance(order, OrderLike)

    def test_broker_protocol_requires_all_fourteen_methods(self):
        for missing_method in _ALL_BROKER_METHODS:
            methods = {name: (lambda *a, **kw: None) for name in _ALL_BROKER_METHODS if name != missing_method}
            Incomplete = type("Incomplete", (), methods)
            assert not isinstance(Incomplete(), Broker), f"缺 {missing_method} 不該還滿足 Broker Protocol"


class TestStrategyRunnerBrokerFieldIsGeneralized:
    def test_broker_field_type_annotation_is_the_broker_protocol_not_paper_broker(self):
        broker_field = next(f for f in StrategyRunner.__dataclass_fields__.values() if f.name == "broker")
        assert broker_field.type == "Broker"

    def test_default_factory_still_builds_a_paper_broker(self):
        broker_field = next(f for f in StrategyRunner.__dataclass_fields__.values() if f.name == "broker")
        assert isinstance(broker_field.default_factory(), PaperBroker)
