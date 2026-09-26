"""live/select_strategy.py:互動式選策略 + 寫回 live_execution_config.yaml
——只測試「寫回 YAML」這個有真實副作用的邏輯
(update_strategy_path_in_config),不測試 main() 本身(那只是把
dsl/discovery.py 跟這個函式黏起來的 CLI 入口,互動式選單的邏輯已經在
test_dsl_discovery.py 測過)。

刻意不放進 live/main.py:main() 必須能無人值守啟動(被 launchd/systemd
這種排程器叫起來時不會有人在終端機前),用 input() 會讓它卡死在那裡等
一個永遠不會來的輸入。這個工具反過來是「人在電腦前先跑一次選好策略」,
選完就結束,不常駐。"""

import yaml

from strategy_lab.live.select_strategy import update_strategy_path_in_config


class TestUpdateStrategyPathInConfig:
    def test_creates_config_from_example_when_missing(self, tmp_path):
        example = tmp_path / "live_execution_config.example.yaml"
        example.write_text(
            yaml.dump(
                {
                    "strategy_path": "strategies/weekend_mean_reversion.yaml",
                    "dry_run": True,
                    "testnet": True,
                }
            )
        )
        config_path = tmp_path / "live_execution_config.yaml"

        update_strategy_path_in_config(config_path, example, "strategies/ma_crossover_bracket.yaml")

        assert config_path.exists()
        data = yaml.safe_load(config_path.read_text())
        assert data["strategy_path"] == "strategies/ma_crossover_bracket.yaml"
        assert data["dry_run"] is True  # 其他欄位照抄範本的安全預設值
        assert data["testnet"] is True

    def test_updates_existing_config_preserving_other_fields(self, tmp_path):
        example = tmp_path / "live_execution_config.example.yaml"
        example.write_text(yaml.dump({"strategy_path": "strategies/weekend_mean_reversion.yaml", "dry_run": True}))
        config_path = tmp_path / "live_execution_config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "strategy_path": "strategies/weekend_mean_reversion.yaml",
                    "dry_run": False,  # 使用者自己已經改過的值
                    "testnet": False,
                }
            )
        )

        update_strategy_path_in_config(config_path, example, "strategies/mean_reversion_breakout_guard.yaml")

        data = yaml.safe_load(config_path.read_text())
        assert data["strategy_path"] == "strategies/mean_reversion_breakout_guard.yaml"
        assert data["dry_run"] is False  # 沒被覆蓋掉
        assert data["testnet"] is False  # 沒被覆蓋掉

    def test_written_file_ends_with_newline(self, tmp_path):
        example = tmp_path / "live_execution_config.example.yaml"
        example.write_text(yaml.dump({"strategy_path": "strategies/weekend_mean_reversion.yaml"}))
        config_path = tmp_path / "live_execution_config.yaml"

        update_strategy_path_in_config(config_path, example, "strategies/ma_crossover_bracket.yaml")

        assert config_path.read_text().endswith("\n")


class TestSelectStrategyConfigArgument:
    def test_writes_chosen_strategy_into_the_given_config_file(self, tmp_path, monkeypatch):
        import strategy_lab.live.select_strategy as module

        target = tmp_path / "live_wld_short.yaml"
        monkeypatch.setattr(module, "prompt_strategy_choice", lambda files: module.STRATEGIES_DIR / "weekend_mean_reversion.yaml")

        module.main(["--config", str(target)])

        data = yaml.safe_load(target.read_text())
        assert data["strategy_path"] == "strategies/weekend_mean_reversion.yaml"
        assert data["dry_run"] is True  # 新檔從 example 範本建立,安全預設值
        assert not (module.CONFIG_PATH == target)
