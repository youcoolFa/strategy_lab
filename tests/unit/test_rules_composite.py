"""rules/composite.py 的 And/Or/Not 測試,用簡單的 stub 條件,不依賴任何
具體業務邏輯。"""

from datetime import datetime, timezone

from strategy_lab.interfaces import StrategyContext
from strategy_lab.rules.composite import And, Not, Or


def make_ctx() -> StrategyContext:
    return StrategyContext(now=datetime(2026, 1, 1, tzinfo=timezone.utc), price=100.0)


class AlwaysTrue:
    def evaluate(self, ctx: StrategyContext) -> bool:
        return True


class AlwaysFalse:
    def evaluate(self, ctx: StrategyContext) -> bool:
        return False


class TestAnd:
    def test_true_when_all_true(self):
        assert And(AlwaysTrue(), AlwaysTrue()).evaluate(make_ctx()) is True

    def test_false_when_any_false(self):
        assert And(AlwaysTrue(), AlwaysFalse()).evaluate(make_ctx()) is False


class TestOr:
    def test_true_when_any_true(self):
        assert Or(AlwaysFalse(), AlwaysTrue()).evaluate(make_ctx()) is True

    def test_false_when_all_false(self):
        assert Or(AlwaysFalse(), AlwaysFalse()).evaluate(make_ctx()) is False


class TestNot:
    def test_negates_true(self):
        assert Not(AlwaysTrue()).evaluate(make_ctx()) is False

    def test_negates_false(self):
        assert Not(AlwaysFalse()).evaluate(make_ctx()) is True


class TestNestedTree:
    def test_and_of_or(self):
        tree = And(Or(AlwaysFalse(), AlwaysTrue()), AlwaysTrue())
        assert tree.evaluate(make_ctx()) is True

    def test_flip_or_to_and_changes_result(self):
        # 呼應 Phase 2 的「Done when」條件:把 Or 換成 And,結果應該立刻翻轉。
        or_tree = Or(AlwaysFalse(), AlwaysTrue())
        and_tree = And(AlwaysFalse(), AlwaysTrue())
        assert or_tree.evaluate(make_ctx()) is True
        assert and_tree.evaluate(make_ctx()) is False
