"""Streamlit 第二階段(實盤啟動 / 改參數 / 停止)背後的動作層 strategy_lab/ui/actions.py(2026-10-09)。
頁面只是外殼:預覽與啟動走 preflight.main、運作中改參數走 control.main、停止 / 脫離走 daemon,
跟終端機版同一套檢查。這裡全部用假的 client / start_fn,不碰網路、不啟動任何程式。"""

import time
from pathlib import Path

import pytest
import yaml

from strategy_lab.ui import actions

CONFIG_TEXT = """# 範本註解
strategy_path: strategies/scale_in_ladder.yaml  # 要跑哪個策略
symbol_override: SUIUSDT   # 幣種
origin_price: null
entry_prices: [1.2297, 1.2291, 1.2285]  # 每注建倉價
dry_run: true   # 先觀察
testnet: false
order_type: limit
position_sizing:
  mode: fixed_qty       # 模式
  value: 20   # 第一注 20 SUI
adopt_existing_position: false
"""


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "live_test.yaml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    return path


class TestSetConfigValues:
    def test_changes_values_and_keeps_comments(self, config):
        actions.set_config_values(config, {"symbol_override": "WLDUSDT", "dry_run": False,
                                           "position_sizing.value": 10.5, "strategy_path": "strategies/x.yaml"})
        text = config.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        assert data["symbol_override"] == "WLDUSDT" and data["dry_run"] is False
        assert data["position_sizing"] == {"mode": "fixed_qty", "value": 10.5}
        assert data["strategy_path"] == "strategies/x.yaml"
        assert "# 範本註解" in text and "# 幣種" in text and "# 第一注 20 SUI" in text and "# 要跑哪個策略" in text
        assert data["entry_prices"] == [1.2297, 1.2291, 1.2285]  # 沒動的不變

    def test_null_and_missing_key_is_appended(self, config):
        actions.set_config_values(config, {"symbol_override": None, "adopt_existing_position": True})
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
        assert data["symbol_override"] is None and data["adopt_existing_position"] is True

    def test_unknown_key_rejected(self, config):
        with pytest.raises(ValueError, match="不能"):
            actions.set_config_values(config, {"api_key": "x"})
        assert config.read_text(encoding="utf-8") == CONFIG_TEXT

    def test_refuses_while_the_daemon_is_running(self, config, monkeypatch):
        monkeypatch.setattr(actions, "daemon_running", lambda path: True)
        with pytest.raises(ValueError, match="在跑"):
            actions.set_config_values(config, {"dry_run": False})
        assert config.read_text(encoding="utf-8") == CONFIG_TEXT


class TestNewConfig:
    def test_creates_live_yaml_from_the_example_template(self, tmp_path):
        example = tmp_path / "live_execution_config.example.yaml"
        example.write_text("dry_run: true\ntestnet: true\n")
        path = actions.new_config(tmp_path, "wld_long", example)
        assert path.name == "live_wld_long.yaml" and path.read_text() == example.read_text()

    @pytest.mark.parametrize("name", ["", "../x", "a b", "execution_config"])
    def test_bad_or_existing_names_rejected(self, tmp_path, name):
        example = tmp_path / "live_execution_config.example.yaml"
        example.write_text("dry_run: true\n")
        (tmp_path / "live_execution_config.yaml").write_text("x: 1\n")
        with pytest.raises(ValueError):
            actions.new_config(tmp_path, name, example)


class TestPreflight:
    def test_preview_answers_nothing_so_nothing_starts(self, config, monkeypatch):
        seen = {}

        def fake_main(argv, input_fn, print_fn, start_fn):
            print_fn("【掛單計畫】")
            seen["answer"] = input_fn("\n輸入 yes 確認以真實資金啟動: ")
            print_fn("已取消,沒有啟動。")
            return 0

        monkeypatch.setattr(actions.preflight, "main", fake_main)
        result = actions.preflight_preview(config)
        assert seen["answer"] == ""
        assert result.ready and "【掛單計畫】" in result.text
        assert result.fingerprint == actions.fingerprint(config)

    def test_preview_that_fails_its_checks_is_not_ready(self, config, monkeypatch):
        def fake_main(argv, input_fn, print_fn, start_fn):
            print_fn("✗ 交易所上 SUIUSDT 還有掛單 3 張")
            return 1

        monkeypatch.setattr(actions.preflight, "main", fake_main)
        assert actions.preflight_preview(config).ready is False

    def test_start_passes_the_typed_answer_and_uses_daemon_start(self, config, monkeypatch):
        seen = {}

        def fake_main(argv, input_fn, print_fn, start_fn):
            seen["argv"], seen["answer"], seen["start_fn"] = argv, input_fn("prompt"), start_fn
            print_fn("已在背景啟動(PID 123)")
            return 0

        monkeypatch.setattr(actions.preflight, "main", fake_main)
        preview = actions.PreviewResult(text="", ready=True, fingerprint=actions.fingerprint(config), at=time.time())
        ok, text = actions.preflight_start(config, preview, typed="yes")
        assert ok and "PID 123" in text
        assert seen["answer"] == "yes" and seen["argv"] == ["--config", str(config)]
        assert seen["start_fn"] is actions.daemon.start

    def test_start_refused_without_a_fresh_matching_preview(self, config, monkeypatch):
        monkeypatch.setattr(actions.preflight, "main", lambda **k: pytest.fail("不能啟動"))
        fp = actions.fingerprint(config)
        with pytest.raises(ValueError, match="預覽"):
            actions.preflight_start(config, None, typed="yes")
        with pytest.raises(ValueError, match="預覽"):
            actions.preflight_start(config, actions.PreviewResult("", False, fp, time.time()), typed="yes")
        with pytest.raises(ValueError, match="超過"):
            actions.preflight_start(config, actions.PreviewResult("", True, fp, time.time() - 11 * 60), typed="yes")
        stale = actions.PreviewResult("", True, fp, time.time())
        config.write_text(CONFIG_TEXT.replace("20   #", "30   #"), encoding="utf-8")
        with pytest.raises(ValueError, match="改過"):
            actions.preflight_start(config, stale, typed="yes")


class TestControl:
    def test_preview_then_apply_through_control_main(self, config, monkeypatch):
        calls = []

        def fake_main(argv, input_fn, print_fn):
            answer = input_fn("prompt")
            calls.append((argv, answer))
            print_fn("【變更預覽】" if not answer else "✓ 已套用")
            return 0

        monkeypatch.setattr(actions.control, "main", fake_main)
        text = actions.control_preview(config, {"entry_prices": [1.1, 1.09, 0], "distance": 0.3, "loop": None})
        assert "【變更預覽】" in text
        ok, text = actions.control_apply(config, {"entry_prices": [1.1, 1.09, 0], "distance": 0.3, "loop": None},
                                         typed="yes")
        assert ok and "已套用" in text
        argv, answer = calls[-1]
        assert argv == ["--config", str(config), "set", "entry_prices=1.1,1.09,0", "distance=0.3", "loop=null"]
        assert answer == "yes"

    def test_nothing_to_change(self, config):
        with pytest.raises(ValueError, match="沒有"):
            actions.control_preview(config, {})


class TestStopAndDetach:
    def test_stop_requires_typing_STOP(self, config, monkeypatch):
        monkeypatch.setattr(actions.daemon, "stop", lambda path: "已停止")
        with pytest.raises(ValueError, match="STOP"):
            actions.stop(config, typed="stop please")
        assert actions.stop(config, typed="STOP") == "已停止"

    def test_detach_requires_typing_DETACH(self, config, monkeypatch):
        monkeypatch.setattr(actions.daemon, "detach", lambda path: "已脫離")
        with pytest.raises(ValueError, match="DETACH"):
            actions.detach(config, typed="")
        assert actions.detach(config, typed="DETACH") == "已脫離"


class TestStrategyInfo:
    def test_lists_strategies_and_scale_in_lot_count(self, tmp_path):
        root = Path(__file__).resolve().parents[2]
        names = [p.name for p in actions.list_strategies(root)]
        assert "scale_in_ladder.yaml" in names
        info = actions.strategy_info(root / "strategies" / "scale_in_ladder.yaml")
        assert info["scale_in"] is True and info["lots"] == 3


EXAMPLE = """# 範本
strategy_path: strategies/weekend_band_reversion.yaml  # 要跑哪個策略
symbol_override: null  # 幣種
entry_prices: null  # 分注每注建倉價
dry_run: true   # 先觀察
testnet: true   # 測試網
position_sizing:
  mode: fixed_qty       # 模式
  value: 0.001            # 數量
adopt_existing_position: false
"""


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    (r / "live_execution_config.example.yaml").write_text(EXAMPLE, encoding="utf-8")
    (r / "live_execution_config.yaml").write_text(CONFIG_TEXT, encoding="utf-8")  # scale_in_ladder + SUIUSDT
    return r


class TestConfigFor:
    """選策略 + 幣種 → 用哪一份設定檔(使用者不用管設定檔,2026-10-09)。"""

    def test_existing_config_with_same_strategy_and_symbol_is_reused(self, root):
        assert actions.config_for(root, "strategies/scale_in_ladder.yaml", "suiusdt") == root / "live_execution_config.yaml"

    def test_otherwise_a_new_auto_named_config(self, root):
        path = actions.config_for(root, "strategies/scale_in_ladder.yaml", "WLDUSDT")
        assert path == root / "live_scale_in_ladder_wldusdt.yaml" and not path.exists()

    def test_same_symbol_but_other_strategy_is_a_different_config(self, root):
        path = actions.config_for(root, "strategies/weekend_band_reversion.yaml", "SUIUSDT")
        assert path.name == "live_weekend_band_reversion_suiusdt.yaml"

    def test_symbol_is_required(self, root):
        with pytest.raises(ValueError, match="幣種"):
            actions.config_for(root, "strategies/scale_in_ladder.yaml", " ")


class TestPrepareConfig:
    SETTINGS = {"adopt_existing_position": False, "position_sizing.mode": "fixed_qty", "position_sizing.value": 10.0}

    def test_new_config_is_created_from_the_template_with_all_values(self, root):
        path = actions.prepare_config(root, "strategies/scale_in_ladder.yaml", "wldusdt", self.SETTINGS,
                                      {"entry_prices": [0.5, 0.49, 0.0], "distance": 0.3, "loop": 2},
                                      example=root / "live_execution_config.example.yaml")
        assert path.name == "live_scale_in_ladder_wldusdt.yaml"
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        assert data["strategy_path"] == "strategies/scale_in_ladder.yaml" and data["symbol_override"] == "WLDUSDT"
        assert data["position_sizing"] == {"mode": "fixed_qty", "value": 10}
        assert data["entry_prices"] == [0.5, 0.49, 0]
        assert data["strategy_overrides"] == {"loop": 2, "exit_distance": {"value": 0.3, "unit": "pct"}}
        assert data["dry_run"] is False and data["testnet"] is False  # 頁面只做真正的交易環境(2026-10-09)
        assert "# 幣種" in text and "# 數量" in text  # 範本的註解保留

    def test_existing_config_is_updated_in_place(self, config):
        root = config.parent
        (root / "live_execution_config.example.yaml").write_text(EXAMPLE, encoding="utf-8")
        path = actions.prepare_config(root, "strategies/scale_in_ladder.yaml", "SUIUSDT",
                                      dict(self.SETTINGS, **{"position_sizing.value": 30.0}),
                                      {"entry_prices": [1.1, 1.09, 1.08]},
                                      example=root / "live_execution_config.example.yaml")
        assert path == config
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
        assert data["position_sizing"]["value"] == 30 and data["entry_prices"] == [1.1, 1.09, 1.08]
        assert "strategy_overrides" not in data  # 沒給 distance / loop 就不寫

    def test_refuses_while_that_config_is_running(self, root, monkeypatch):
        monkeypatch.setattr(actions, "daemon_running", lambda path: True)
        before = (root / "live_execution_config.yaml").read_text(encoding="utf-8")
        with pytest.raises(ValueError, match="在跑"):
            actions.prepare_config(root, "strategies/scale_in_ladder.yaml", "SUIUSDT", self.SETTINGS, {},
                                   example=root / "live_execution_config.example.yaml")
        assert (root / "live_execution_config.yaml").read_text(encoding="utf-8") == before

    def test_bad_entry_prices_are_rejected_before_anything_is_written(self, root):
        with pytest.raises(ValueError):
            actions.prepare_config(root, "strategies/scale_in_ladder.yaml", "WLDUSDT", self.SETTINGS,
                                   {"entry_prices": [0, 0.49, 0.48]},
                                   example=root / "live_execution_config.example.yaml")
        assert not (root / "live_scale_in_ladder_wldusdt.yaml").exists()


class TestPageIsLiveOnly:
    """頁面只做真正的交易環境(2026-10-09 使用者要求):從頁面開始的策略一律 dry_run: false、testnet: false,
    頁面也不能傳這兩個值進來。dry-run / 測試網仍可從終端機用。"""

    def test_existing_dry_run_config_becomes_live_when_prepared_from_the_page(self, config):
        root = config.parent
        (root / "live_execution_config.example.yaml").write_text(EXAMPLE, encoding="utf-8")
        assert yaml.safe_load(config.read_text(encoding="utf-8"))["dry_run"] is True
        actions.prepare_config(root, "strategies/scale_in_ladder.yaml", "SUIUSDT", TestPrepareConfig.SETTINGS, {},
                               example=root / "live_execution_config.example.yaml")
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
        assert data["dry_run"] is False and data["testnet"] is False

    @pytest.mark.parametrize("key", ["dry_run", "testnet"])
    def test_page_cannot_pass_dry_run_or_testnet(self, root, key):
        with pytest.raises(ValueError, match=key):
            actions.prepare_config(root, "strategies/scale_in_ladder.yaml", "WLDUSDT",
                                   dict(TestPrepareConfig.SETTINGS, **{key: True}), {},
                                   example=root / "live_execution_config.example.yaml")
        assert not (root / "live_scale_in_ladder_wldusdt.yaml").exists()
