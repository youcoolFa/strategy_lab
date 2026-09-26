"""live/bybit_client.py 的邏輯測試——用假的 http_client(不是真的
pybit.unified_trading.HTTP,是手寫的 stub),不需要任何真實 API key、
不會打真正的網路請求。每個方法對應 sat_strategy/app/bot.py 原本呼叫
ccxt 的其中一個方法,一對一改寫。"""

import pytest
from pybit.exceptions import FailedRequestError, InvalidRequestError

from strategy_lab.live.bybit_client import BybitAPIError, BybitClient, OrderResult


class FakeHTTP:
    """手寫的假 pybit HTTP client,而不是用 mock 物件——這樣每個測試
    可以直接看到餵進去、吐出來的資料長什麼樣,不用中間再查 mock 的呼叫
    紀錄。"""

    def __init__(self):
        self.calls = []
        self.tickers_response = None
        self.place_order_response = None
        self.open_orders_response = {"result": {"list": []}}
        self.order_history_response = {"result": {"list": []}}
        self.cancel_order_response = {"result": {}}
        self.positions_response = {"result": {"list": []}}
        self.instruments_info_response = None
        self.wallet_balance_response = None
        self.raise_on_next_call = None

    def _maybe_raise(self):
        if self.raise_on_next_call is not None:
            exc = self.raise_on_next_call
            self.raise_on_next_call = None
            raise exc

    def get_tickers(self, **kwargs):
        self.calls.append(("get_tickers", kwargs))
        self._maybe_raise()
        return self.tickers_response

    def place_order(self, **kwargs):
        self.calls.append(("place_order", kwargs))
        self._maybe_raise()
        return self.place_order_response

    def get_open_orders(self, **kwargs):
        self.calls.append(("get_open_orders", kwargs))
        self._maybe_raise()
        return self.open_orders_response

    def get_order_history(self, **kwargs):
        self.calls.append(("get_order_history", kwargs))
        self._maybe_raise()
        return self.order_history_response

    def cancel_order(self, **kwargs):
        self.calls.append(("cancel_order", kwargs))
        self._maybe_raise()
        return self.cancel_order_response

    def get_positions(self, **kwargs):
        self.calls.append(("get_positions", kwargs))
        self._maybe_raise()
        return self.positions_response

    def get_instruments_info(self, **kwargs):
        self.calls.append(("get_instruments_info", kwargs))
        self._maybe_raise()
        return self.instruments_info_response

    def get_wallet_balance(self, **kwargs):
        self.calls.append(("get_wallet_balance", kwargs))
        self._maybe_raise()
        return self.wallet_balance_response


def make_client(http=None) -> BybitClient:
    return BybitClient(api_key="test", api_secret="test", http_client=http or FakeHTTP())


class TestGetLastPrice:
    def test_returns_last_price_as_float(self):
        http = FakeHTTP()
        http.tickers_response = {"result": {"list": [{"lastPrice": "60123.45"}]}}
        client = make_client(http)

        assert client.get_last_price("BTCUSDT") == 60123.45
        assert http.calls == [("get_tickers", {"category": "linear", "symbol": "BTCUSDT"})]


class TestCategoryIsConfigurable:
    """`category`(spot/linear/inverse/option)原本寫死在模組常數,改成
    建構子參數——預設維持 `linear`(舊行為不變),但可以覆蓋成其他市場
    類型。"""

    def test_defaults_to_linear_when_not_specified(self):
        http = FakeHTTP()
        http.tickers_response = {"result": {"list": [{"lastPrice": "100.0"}]}}
        client = BybitClient(api_key="test", api_secret="test", http_client=http)

        client.get_last_price("BTCUSDT")

        assert http.calls == [("get_tickers", {"category": "linear", "symbol": "BTCUSDT"})]

    def test_overriding_category_is_used_in_every_api_call(self):
        http = FakeHTTP()
        http.tickers_response = {"result": {"list": [{"lastPrice": "1.5"}]}}
        client = BybitClient(api_key="test", api_secret="test", category="spot", http_client=http)

        client.get_last_price("BTCUSDT")

        assert http.calls == [("get_tickers", {"category": "spot", "symbol": "BTCUSDT"})]


class TestPlaceLimitOrder:
    def test_returns_open_order_result(self):
        http = FakeHTTP()
        http.place_order_response = {"result": {"orderId": "order-1"}}
        client = make_client(http)

        result = client.place_limit_order("BTCUSDT", "Buy", 0.01, 60000.0)

        assert result == OrderResult(order_id="order-1", status="open", price=60000.0)
        assert http.calls == [
            (
                "place_order",
                {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "orderType": "Limit",
                    "qty": "0.01",
                    "price": "60000.0",
                    "reduceOnly": False,
                    "timeInForce": "GTC",
                },
            )
        ]

    def test_reduce_only_flag_is_forwarded(self):
        http = FakeHTTP()
        http.place_order_response = {"result": {"orderId": "order-2"}}
        client = make_client(http)

        client.place_limit_order("BTCUSDT", "Sell", 0.01, 61000.0, reduce_only=True)

        assert http.calls[0][1]["reduceOnly"] is True


class TestPlaceMarketOrder:
    def test_returns_open_order_result(self):
        http = FakeHTTP()
        http.place_order_response = {"result": {"orderId": "order-3"}}
        client = make_client(http)

        result = client.place_market_order("BTCUSDT", "Sell", 0.01, reduce_only=True)

        assert result == OrderResult(order_id="order-3", status="open", price=0.0)
        assert http.calls == [
            (
                "place_order",
                {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "side": "Sell",
                    "orderType": "Market",
                    "qty": "0.01",
                    "reduceOnly": True,
                },
            )
        ]


class TestGetOrderStatus:
    def test_returns_open_when_found_in_open_orders(self):
        http = FakeHTTP()
        http.open_orders_response = {
            "result": {"list": [{"orderId": "o1", "price": "60000.0", "cumExecQty": "0"}]}
        }
        client = make_client(http)

        result = client.get_order_status("BTCUSDT", "o1")
        assert result == OrderResult(order_id="o1", status="open", price=60000.0, filled_qty=0.0)

    def test_falls_back_to_history_when_not_in_open_orders(self):
        http = FakeHTTP()
        http.open_orders_response = {"result": {"list": []}}
        http.order_history_response = {
            "result": {
                "list": [
                    {"orderId": "o1", "orderStatus": "Filled", "price": "60000.0", "avgPrice": "59998.5", "cumExecQty": "0.01"}
                ]
            }
        }
        client = make_client(http)

        result = client.get_order_status("BTCUSDT", "o1")
        # 用 avgPrice(實際成交均價),不是 price(掛單當初的限價)——兩者
        # 通常很接近,但成交均價才是計算損益該用的數字。
        assert result == OrderResult(order_id="o1", status="closed", price=59998.5, filled_qty=0.01)

    def test_falls_back_to_price_when_avg_price_is_zero(self):
        """avgPrice 是 "0"(字串)的情況——Bybit 對某些已終結但非成交的
        狀態(例如 Cancelled)可能回傳 avgPrice="0",這時候要退回用
        price,不能把 0 當成真的成交價。"""
        http = FakeHTTP()
        http.open_orders_response = {"result": {"list": []}}
        http.order_history_response = {
            "result": {
                "list": [
                    {"orderId": "o1", "orderStatus": "Cancelled", "price": "60000.0", "avgPrice": "0", "cumExecQty": "0"}
                ]
            }
        }
        client = make_client(http)

        result = client.get_order_status("BTCUSDT", "o1")
        assert result.price == 60000.0

    def test_cancelled_order_maps_to_canceled_status(self):
        http = FakeHTTP()
        http.open_orders_response = {"result": {"list": []}}
        http.order_history_response = {
            "result": {"list": [{"orderId": "o1", "orderStatus": "Cancelled", "cumExecQty": "0"}]}
        }
        client = make_client(http)

        result = client.get_order_status("BTCUSDT", "o1")
        assert result.status == "canceled"

    def test_raises_when_order_not_found_anywhere(self):
        http = FakeHTTP()
        http.open_orders_response = {"result": {"list": []}}
        http.order_history_response = {"result": {"list": []}}
        client = make_client(http)

        with pytest.raises(BybitAPIError):
            client.get_order_status("BTCUSDT", "does-not-exist")


class TestCancelOrder:
    def test_calls_cancel_with_correct_params(self):
        http = FakeHTTP()
        client = make_client(http)

        client.cancel_order("BTCUSDT", "o1")

        assert http.calls == [("cancel_order", {"category": "linear", "symbol": "BTCUSDT", "orderId": "o1"})]

    def test_order_not_found_is_swallowed(self):
        http = FakeHTTP()
        http.raise_on_next_call = InvalidRequestError(
            request="req", message="Order does not exist", status_code=110001, time="t", resp_headers=None
        )
        client = make_client(http)

        client.cancel_order("BTCUSDT", "already-gone")  # 不應該 raise

    def test_other_invalid_request_errors_propagate(self):
        http = FakeHTTP()
        http.raise_on_next_call = InvalidRequestError(
            request="req", message="Insufficient balance", status_code=110007, time="t", resp_headers=None
        )
        client = make_client(http)

        with pytest.raises(InvalidRequestError):
            client.cancel_order("BTCUSDT", "o1")


class TestGetPositionQty:
    def test_sums_position_sizes(self):
        http = FakeHTTP()
        http.positions_response = {"result": {"list": [{"side": "Buy", "size": "0.03"}]}}
        client = make_client(http)

        assert client.get_position_qty("BTCUSDT") == 0.03

    def test_short_position_is_negative(self):
        # Bybit 的 size 永遠是正數,方向在 side。空單要回傳負數,
        # runner._cleanup() 靠正負號決定用買回還是賣出平倉。
        http = FakeHTTP()
        http.positions_response = {"result": {"list": [{"side": "Sell", "size": "41.1"}]}}
        client = make_client(http)

        assert client.get_position_qty("WLDUSDT") == -41.1

    def test_flat_one_way_position_with_empty_side_is_zero(self):
        # 真實 mainnet 回傳:沒持倉時 side 是空字串、size 是 "0"。
        http = FakeHTTP()
        http.positions_response = {"result": {"list": [{"side": "", "size": "0", "positionIdx": 0}]}}
        client = make_client(http)

        assert client.get_position_qty("WLDUSDT") == 0.0

    def test_returns_zero_when_no_position(self):
        http = FakeHTTP()
        http.positions_response = {"result": {"list": []}}
        client = make_client(http)

        assert client.get_position_qty("BTCUSDT") == 0.0


class TestGetOpenOrders:
    def test_returns_raw_open_order_list_for_symbol(self):
        http = FakeHTTP()
        http.open_orders_response = {
            "result": {"list": [{"orderId": "o1", "side": "Buy", "price": "0.4764", "qty": "41.1"}]}
        }
        client = make_client(http)

        orders = client.get_open_orders("WLDUSDT")

        assert orders == [{"orderId": "o1", "side": "Buy", "price": "0.4764", "qty": "41.1"}]
        assert http.calls == [("get_open_orders", {"category": "linear", "symbol": "WLDUSDT"})]

    def test_empty_when_no_open_orders(self):
        client = make_client(FakeHTTP())
        assert client.get_open_orders("WLDUSDT") == []


class TestGetInstrumentInfo:
    def test_returns_the_single_instrument_dict(self):
        http = FakeHTTP()
        http.instruments_info_response = {
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "priceFilter": {"minPrice": "0.10", "maxPrice": "1999999.80", "tickSize": "0.10"},
                        "lotSizeFilter": {
                            "maxOrderQty": "1500.000",
                            "minOrderQty": "0.001",
                            "qtyStep": "0.001",
                            "maxMktOrderQty": "150.000",
                            "minNotionalValue": "5",
                        },
                    }
                ]
            }
        }
        client = make_client(http)

        result = client.get_instrument_info("BTCUSDT")

        assert result["symbol"] == "BTCUSDT"
        assert result["lotSizeFilter"]["qtyStep"] == "0.001"
        assert http.calls == [("get_instruments_info", {"category": "linear", "symbol": "BTCUSDT"})]


class TestGetAccountEquity:
    def test_returns_total_equity_as_float(self):
        http = FakeHTTP()
        http.wallet_balance_response = {
            "result": {
                "list": [
                    {
                        "accountType": "UNIFIED",
                        "totalEquity": "59.9654892",
                        "coin": [{"coin": "USDT", "equity": "59.97658487"}],
                    }
                ]
            }
        }
        client = make_client(http)

        assert client.get_account_equity() == 59.9654892
        assert http.calls == [("get_wallet_balance", {"accountType": "UNIFIED"})]

    def test_returns_zero_when_no_account_data(self):
        """帳戶還沒有任何資產(全新帳戶)時,Bybit 可能回傳空清單——不該
        讓呼叫端崩潰,回傳 0 讓上層(fix_qty 之類)自己決定怎麼處理。"""
        http = FakeHTTP()
        http.wallet_balance_response = {"result": {"list": []}}
        client = make_client(http)

        assert client.get_account_equity() == 0.0


class TestRetryOnNetworkFailure:
    def test_retries_on_failed_request_error_then_succeeds(self):
        http = FakeHTTP()
        http.tickers_response = {"result": {"list": [{"lastPrice": "100.0"}]}}

        call_count = {"n": 0}
        original_get_tickers = http.get_tickers

        def flaky_get_tickers(**kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise FailedRequestError(request="req", message="network blip", status_code=None, time="t", resp_headers=None)
            return original_get_tickers(**kwargs)

        http.get_tickers = flaky_get_tickers
        client = BybitClient(api_key="test", api_secret="test", http_client=http, max_retries=5, retry_backoff_cap_seconds=0.01)

        assert client.get_last_price("BTCUSDT") == 100.0
        assert call_count["n"] == 3

    def test_gives_up_after_max_retries(self):
        http = FakeHTTP()

        def always_fails(**kwargs):
            raise FailedRequestError(request="req", message="down", status_code=None, time="t", resp_headers=None)

        http.get_tickers = always_fails
        client = BybitClient(api_key="test", api_secret="test", http_client=http, max_retries=3, retry_backoff_cap_seconds=0.01)

        with pytest.raises(FailedRequestError):
            client.get_last_price("BTCUSDT")


class TestSafeDefaults:
    def test_testnet_defaults_to_true(self):
        # 建構子預設值本身要是安全的(testnet=True)——實際要不要對接
        # mainnet,是呼叫端明確傳參數的責任,不是這個 class 悄悄預設。
        import inspect

        sig = inspect.signature(BybitClient.__init__)
        assert sig.parameters["testnet"].default is True
