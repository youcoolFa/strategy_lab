"""live/fetch_instrument_limits.py:下載 Bybit 商品精度限制寫進
instrument_limits.json——只測不碰真實網路的部分(從 strategies/*.yaml
找出要抓哪些 symbol、把 BybitClient 回傳的資料寫進檔案),不測真的打
API 那一段(那是公開端點,已經在專案根目錄留了一份真實下載結果當
regression 用)。"""

import json

from strategy_lab.live.fetch_instrument_limits import _symbols_used_by_strategies, fetch_and_save


class TestSymbolsUsedByStrategies:
    def test_finds_btcusdt_from_all_strategy_yamls(self):
        # 三份策略 YAML 目前都是 BTC/USDT,轉成 Bybit 格式後應該只有一個。
        assert _symbols_used_by_strategies() == ["BTCUSDT"]


class FakeBybitClientForFetch:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_instrument_info(self, symbol):
        self.calls.append(symbol)
        return self.responses[symbol]


class TestFetchAndSave:
    def test_writes_one_entry_per_symbol(self, tmp_path, monkeypatch):
        import strategy_lab.live.fetch_instrument_limits as mod

        fake_client = FakeBybitClientForFetch({"BTCUSDT": {"symbol": "BTCUSDT", "priceFilter": {}}})
        monkeypatch.setattr(mod, "BybitClient", lambda **kwargs: fake_client)
        output_path = tmp_path / "instrument_limits.json"

        fetch_and_save(["BTCUSDT"], output_path=output_path)

        data = json.loads(output_path.read_text())
        assert data == {"BTCUSDT": {"symbol": "BTCUSDT", "priceFilter": {}}}

    def test_preserves_existing_symbols_not_being_refetched(self, tmp_path, monkeypatch):
        """重跑下載腳本只更新這次指定的 symbol,不會把檔案裡其他
        symbol 的資料清掉。"""
        import strategy_lab.live.fetch_instrument_limits as mod

        output_path = tmp_path / "instrument_limits.json"
        output_path.write_text(json.dumps({"ETHUSDT": {"symbol": "ETHUSDT"}}))

        fake_client = FakeBybitClientForFetch({"BTCUSDT": {"symbol": "BTCUSDT"}})
        monkeypatch.setattr(mod, "BybitClient", lambda **kwargs: fake_client)

        fetch_and_save(["BTCUSDT"], output_path=output_path)

        data = json.loads(output_path.read_text())
        assert data["ETHUSDT"] == {"symbol": "ETHUSDT"}
        assert data["BTCUSDT"] == {"symbol": "BTCUSDT"}
