"""interfaces.py 新增的 Broker/OrderLike Protocol——泛化 StrategyRunner
的 broker 欄位,讓它不再寫死 PaperBroker 型別。這一步刻意不改變任何
現有行為:PaperBroker 現有的 7 個方法(place_limit_buy/place_limit_sell/
cancel_order/fetch_order/position_qty/market_close/tick)結構上已經
滿足這個 Protocol,不需要改 PaperBroker 一行程式碼。"""

import inspect

from strategy_lab.broker.paper_broker import Order, PaperBroker
from strategy_lab.engine.runner import StrategyRunner
from strategy_lab.interfaces import Broker, OrderLike


class TestBrokerProtocolConformance:
    def test_paper_broker_satisfies_broker_protocol_without_any_changes(self):
        assert isinstance(PaperBroker(), Broker)

    def test_paper_broker_order_satisfies_order_like_protocol(self):
        order = Order(id="1", side="buy", price=100.0, qty=1.0)
        assert isinstance(order, OrderLike)

    def test_broker_protocol_requires_all_seven_methods(self):
        class Incomplete:
            def place_limit_buy(self, price, qty):
                ...

            def place_limit_sell(self, price, qty):
                ...

            def fetch_order(self, order_id):
                ...

            def cancel_order(self, order_id):
                ...

            def position_qty(self):
                ...

            def market_close(self, qty):
                ...
            # 故意漏掉 tick()

        assert not isinstance(Incomplete(), Broker)


class TestStrategyRunnerBrokerFieldIsGeneralized:
    def test_broker_field_type_annotation_is_the_broker_protocol_not_paper_broker(self):
        broker_field = next(f for f in StrategyRunner.__dataclass_fields__.values() if f.name == "broker")
        assert broker_field.type == "Broker"

    def test_default_factory_still_builds_a_paper_broker(self):
        broker_field = next(f for f in StrategyRunner.__dataclass_fields__.values() if f.name == "broker")
        assert isinstance(broker_field.default_factory(), PaperBroker)
