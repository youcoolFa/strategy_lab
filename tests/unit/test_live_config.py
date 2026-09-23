"""live/config.py 的邏輯測試——對應 sat_strategy/app/config.py 的
StrategyConfig/load_config() 同一種設計:dataclass 預設值本身要是安全的
(dry_run=True、testnet=True、use_live_ticker_feed=False,逐項對照
sat_strategy 自己的預設值),JSON 設定檔 + 環境變數才能覆蓋成真的要上線
的樣子。這裡不測試策略參數(symbol/order_qty/進出場邏輯那些)——那些
從 strategies/*.yaml 透過 dsl.loader 讀,不重複放在這個設定檔裡。"""

import json

from strategy_lab.live.config import ExecutionConfig, load_execution_config


class TestExecutionConfigDefaults:
    def test_dry_run_defaults_to_true(self):
        assert ExecutionConfig().dry_run is True

    def test_testnet_defaults_to_true(self):
        assert ExecutionConfig().testnet is True

    def test_use_live_ticker_feed_defaults_to_false(self):
        # 跟 sat_strategy/app/config.py 的理由一樣:Fa_Successful_trade
        # 目前接的網路(mainnet/testnet)不一定跟這裡的設定一致,預設
        # 關閉,避免用錯網路的即時價格誤導策略判斷。
        assert ExecutionConfig().use_live_ticker_feed is False

    def test_strategy_path_defaults_to_weekend_mean_reversion(self):
        assert ExecutionConfig().strategy_path == "strategies/weekend_mean_reversion.yaml"


class TestLoadExecutionConfigFromJson:
    def test_missing_file_returns_pure_defaults(self, tmp_path):
        config = load_execution_config(config_path=tmp_path / "does_not_exist.json")
        assert config == ExecutionConfig()

    def test_json_overrides_are_applied(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"dry_run": False, "testnet": False, "strategy_path": "strategies/ma_crossover_bracket.yaml"}))

        config = load_execution_config(config_path=path)

        assert config.dry_run is False
        assert config.testnet is False
        assert config.strategy_path == "strategies/ma_crossover_bracket.yaml"

    def test_fields_not_in_json_keep_their_default(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"dry_run": False}))

        config = load_execution_config(config_path=path)

        assert config.dry_run is False
        assert config.testnet is True  # 沒寫在 JSON 裡,維持安全預設

    def test_unknown_json_key_is_ignored_not_raised(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"typo_field": "oops"}))

        config = load_execution_config(config_path=path)  # 不應該 raise

        assert config == ExecutionConfig()


class TestLoadExecutionConfigFromEnv:
    def test_dry_run_env_var_overrides_json(self, tmp_path, monkeypatch):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"dry_run": False}))
        monkeypatch.setenv("STRATEGY_LAB_DRY_RUN", "true")

        config = load_execution_config(config_path=path)

        assert config.dry_run is True

    def test_testnet_env_var_accepts_common_truthy_strings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STRATEGY_LAB_TESTNET", "0")
        config = load_execution_config(config_path=tmp_path / "missing.json")
        assert config.testnet is False

    def test_use_live_feed_env_var_overrides_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STRATEGY_LAB_USE_LIVE_FEED", "yes")
        config = load_execution_config(config_path=tmp_path / "missing.json")
        assert config.use_live_ticker_feed is True
