"""Streamlit 第二階段(實盤啟動 / 改參數 / 停止)背後的動作層 strategy_lab/ui/actions.py(2026-10-09)。
頁面只是外殼:預覽與啟動走 preflight.main、運作中改參數走 control.main、停止 / 交接走 daemon,
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


class TestStopAndHandover:
    def test_stop_requires_typing_STOP(self, config, monkeypatch):
        monkeypatch.setattr(actions.daemon, "stop", lambda path: "已停止")
        with pytest.raises(ValueError, match="STOP"):
            actions.stop(config, typed="stop please")
        assert actions.stop(config, typed="STOP") == "已停止"

    def test_handover_requires_typing_HANDOVER(self, config, monkeypatch):
        monkeypatch.setattr(actions.daemon, "handover", lambda path: "已交接")
        with pytest.raises(ValueError, match="HANDOVER"):
            actions.handover(config, typed="")
        assert actions.handover(config, typed="HANDOVER") == "已交接"


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


class TestBandStrategyFromThePage:
    """區間策略(2026-10-10):頁面填買賣 %,寫進設定檔的 strategy_overrides.band;不寫 loop(一定做到收尾)。"""

    def test_strategy_info_reports_band_and_its_pct(self):
        info = actions.strategy_info(Path(__file__).resolve().parents[2] / "strategies" / "weekend_band_reversion.yaml")
        assert info["band"] is True and info["scale_in"] is False
        assert info["buy_pct"] > 0 and info["sell_pct"] > 0

    def test_other_strategies_are_not_band(self):
        info = actions.strategy_info(Path(__file__).resolve().parents[2] / "strategies" / "scale_in_ladder.yaml")
        assert info["band"] is False

    def test_prepare_config_writes_band_pct_and_no_loop(self, root):
        path = actions.prepare_config(root, "strategies/weekend_band_reversion.yaml", "xrpusdt", TestPrepareConfig.SETTINGS,
                                      {"band": {"buy_pct": 0.3, "sell_pct": 0.5}, "loop": 3},
                                      example=root / "live_execution_config.example.yaml")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert path.name == "live_weekend_band_reversion_xrpusdt.yaml"
        assert data["strategy_overrides"] == {"band": {"buy_pct": 0.3, "sell_pct": 0.5}}
        assert data["dry_run"] is False and data["testnet"] is False

    def test_prepare_config_writes_direction(self, root):
        path = actions.prepare_config(root, "strategies/weekend_short_breakout.yaml", "eth", TestPrepareConfig.SETTINGS,
                                      {"direction": "long", "loop": 0}, example=root / "live_execution_config.example.yaml")
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["strategy_overrides"] == {"direction": "long", "loop": 0}
        with pytest.raises(ValueError, match="long 或 short"):
            actions.prepare_config(root, "strategies/scale_in_ladder.yaml", "ETH", TestPrepareConfig.SETTINGS,
                                   {"direction": "both"}, example=root / "live_execution_config.example.yaml")

    def test_bad_band_pct_rejected_before_writing(self, root):
        with pytest.raises(ValueError, match="buy_pct"):
            actions.prepare_config(root, "strategies/weekend_band_reversion.yaml", "XRPUSDT", TestPrepareConfig.SETTINGS,
                                   {"band": {"buy_pct": 0, "sell_pct": 0.5}},
                                   example=root / "live_execution_config.example.yaml")
        assert not (root / "live_weekend_band_reversion_xrpusdt.yaml").exists()


class TestAccountConfig:
    """多帳戶(2026-10-12):策略 + 幣種 + 帳戶 → 一份設定檔;帳戶寫進設定檔的 account:。"""

    def test_existing_config_without_account_belongs_to_test1(self, root):
        path = root / "live_execution_config.yaml"
        assert actions.config_for(root, "strategies/scale_in_ladder.yaml", "SUIUSDT", "TEST1") == path
        assert actions.config_for(root, "strategies/scale_in_ladder.yaml", "SUIUSDT") == path

    def test_other_account_gets_its_own_config(self, root):
        path = actions.config_for(root, "strategies/scale_in_ladder.yaml", "SUIUSDT", "MAIN")
        assert path.name == "live_scale_in_ladder_suiusdt_main.yaml" and not path.exists()

    def test_existing_config_matched_by_account_case_insensitively(self, root):
        other = root / "live_mine.yaml"
        other.write_text("strategy_path: strategies/scale_in_ladder.yaml\nsymbol_override: SUIUSDT\naccount: MAIN\n")
        assert actions.config_for(root, "strategies/scale_in_ladder.yaml", "SUIUSDT", "main") == other

    def test_prepare_config_writes_the_account(self, root):
        path = actions.prepare_config(root, "strategies/scale_in_ladder.yaml", "SUIUSDT", TestPrepareConfig.SETTINGS,
                                      {}, example=root / "live_execution_config.example.yaml", account="MAIN")
        assert path.name == "live_scale_in_ladder_suiusdt_main.yaml"
        assert yaml.safe_load(path.read_text())["account"] == "MAIN"
        assert "account" not in yaml.safe_load((root / "live_execution_config.yaml").read_text())  # test1 那份沒動


class TestManyAccounts:
    """一次選多個帳戶跑同一個策略(2026-10-12):每個帳戶一份設定檔、一支程式;
    多個帳戶時數量只能用權益百分比;預覽沒過的帳戶不啟動,其他照樣開始(使用者決定)。"""

    PCT = {"position_sizing.mode": "account_percentage", "position_sizing.value": 5.0}

    @pytest.fixture
    def previews(self, monkeypatch):
        """MAIN 的預覽不通過,其他通過;啟動記進 started。"""
        started = []
        monkeypatch.setattr(actions, "preflight_preview", lambda path: actions.PreviewResult(
            text=f"預覽 {Path(path).name}", ready="main" not in Path(path).name, fingerprint="fp", at=time.time()))
        monkeypatch.setattr(actions, "preflight_start", lambda path, preview, typed: started.append(
            (Path(path).name, typed)) or (typed == "yes", "已在背景啟動"))
        return started

    def prepare(self, root, accounts_, settings=None):
        return actions.prepare_many(root, "strategies/scale_in_ladder.yaml", "WLDUSDT", settings or self.PCT,
                                    {}, accounts_, example=root / "live_execution_config.example.yaml")

    def test_each_account_gets_its_own_config_and_preview(self, root, previews):
        plans = self.prepare(root, ["test1", "MAIN"])
        assert [(p.account, p.path.name, p.ready) for p in plans] == [
            ("test1", "live_scale_in_ladder_wldusdt.yaml", True),
            ("MAIN", "live_scale_in_ladder_wldusdt_main.yaml", False)]
        assert yaml.safe_load(plans[1].path.read_text())["account"] == "MAIN"

    def test_more_than_one_account_requires_account_percentage(self, root, previews):
        with pytest.raises(ValueError, match="權益百分比"):
            self.prepare(root, ["test1", "MAIN"], TestPrepareConfig.SETTINGS)
        assert not (root / "live_scale_in_ladder_wldusdt_main.yaml").exists()  # 什麼都沒寫
        assert self.prepare(root, ["test1"], TestPrepareConfig.SETTINGS)[0].ready  # 單一帳戶照舊

    def test_at_least_one_account(self, root, previews):
        with pytest.raises(ValueError, match="帳戶"):
            self.prepare(root, [])

    def test_one_account_failing_to_prepare_does_not_stop_the_others(self, root, previews, monkeypatch):
        real = actions.prepare_config

        def prepare_config(*a, account=None, **k):
            if account == "MAIN":
                raise ValueError("live_scale_in_ladder_wldusdt_main 正在跑")
            return real(*a, account=account, **k)

        monkeypatch.setattr(actions, "prepare_config", prepare_config)
        test1, main = self.prepare(root, ["test1", "MAIN"])
        assert test1.ready and not main.ready and "正在跑" in main.error and main.preview is None

    def test_start_only_the_accounts_that_passed(self, root, previews):
        plans = self.prepare(root, ["test1", "MAIN"])
        results = actions.start_many(plans, "yes")
        assert previews == [("live_scale_in_ladder_wldusdt.yaml", "yes")]
        assert [(r.account, r.ok) for r in results] == [("test1", True), ("MAIN", False)]
        assert "沒有開始" in results[1].text

    def test_nothing_ready_means_nothing_to_start(self, root, previews):
        with pytest.raises(ValueError, match="沒有"):
            actions.start_many(self.prepare(root, ["MAIN"]), "yes")


@pytest.mark.parametrize("typed, symbol", [
    ("eth", "ETHUSDT"), (" ETH ", "ETHUSDT"), ("ethusdt", "ETHUSDT"), ("ETH/USDT", "ETHUSDT"), ("1000pepe", "1000PEPEUSDT"), ("", ""),
])
def test_perp_symbol_only_needs_the_coin(typed, symbol):
    """介面只填幣名,USDT 自動補上(全部是 USDT 永續,2026-10-12)。"""
    assert actions.perp_symbol(typed) == symbol


class TestSavePnlEdits:
    """損益表裡直接改的可後補欄位(預估時長 / 目標價 / 停損價 / 備註)存回 sl_event(2026-10-14)。
    全部驗證過才寫;有一列不對就一列都不寫。清空 = 寫 NULL。"""

    @pytest.fixture
    def db(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        from strategy_lab.engine.events import Event
        from strategy_lab.storage.recorder import RunInfo, TradeRecorder
        from strategy_lab.storage.setup_db import setup

        url = f"sqlite:///{tmp_path / 'trading.db'}"
        setup(url)
        t0 = datetime(2026, 10, 9, tzinfo=timezone.utc)
        rec = TradeRecorder(db_url=url, pending_dir=tmp_path / "p", fetch_executions=lambda *a: [])
        self.run_id = rec.start_run(RunInfo(
            strategy_name="free style", strategy_path="NA", strategy_yaml="NA", strategy_params={}, config={},
            symbol="ETHUSDT", category="linear", direction="NA", origin_price=None, origin_source=None, qty=None,
            order_type="NA", loop=None, testnet=False, preflight=None, log_path=None, started_at=t0, account="MAIN",
            note="原本的"))
        for i, d in ((1, "long"), (2, "short")):
            rec.record_event(Event(index=i, direction=d, start_time=t0 + timedelta(hours=i), end_time=t0 + timedelta(hours=i, minutes=30),
                                   fills=2, max_position=1.0, avg_entry=100.0, avg_exit=101.0, realized_pnl=1.0,
                                   max_drawdown=0.0, forced=False))
        return url

    def events(self, db):
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session

        from strategy_lab.storage.models import SlEvent

        engine = create_engine(db)
        with Session(engine) as s:
            out = {e.event_index: e for e in s.scalars(select(SlEvent))}
        engine.dispose()
        return out

    def edit(self, index, **k):
        return {"run_id": self.run_id, "event_index": index, "預估時長": None, "預估單位": "", "目標價": None, "停損價": None,
                "備註": "", **k}

    def test_saves_and_clears(self, db):
        n = actions.save_pnl_edits(db, [self.edit(1, 預估時長=4, 預估單位="小時", 目標價=110.0, 停損價=95.0, 備註="照計劃"),
                                        self.edit(2)])
        e = self.events(db)
        assert n == 2
        assert (e[1].expected_hold_seconds, float(e[1].target_price), float(e[1].stop_price), e[1].note) == (
            14400, 110.0, 95.0, "照計劃")
        assert e[2].note is None  # 清空原本的「原本的」

    def test_nan_from_the_table_means_empty(self, db):
        actions.save_pnl_edits(db, [self.edit(1, 目標價=float("nan"))])
        assert self.events(db)[1].target_price is None

    @pytest.mark.parametrize("bad,match", [
        ({"預估時長": -1, "預估單位": "小時"}, "預估時長"),
        ({"預估時長": 4, "預估單位": "年"}, "單位"),
        ({"目標價": -1.0}, "目標價"),
        ({"目標價": 90.0, "停損價": 95.0}, "停損價要低於目標價"),  # 第 1 列做多
        ({"備註": "x" * 501}, "備註"),
    ])
    def test_any_bad_row_writes_nothing(self, db, bad, match):
        with pytest.raises(ValueError, match=match):
            actions.save_pnl_edits(db, [self.edit(2, 備註="先改這列"), self.edit(1, **bad)])
        assert self.events(db)[2].note == "原本的"

    def test_short_rows_need_stop_above_target(self, db):
        with pytest.raises(ValueError, match="停損價要高於目標價"):
            actions.save_pnl_edits(db, [self.edit(2, 目標價=110.0, 停損價=95.0)])


def test_pnl_changes_only_returns_rows_whose_editable_cells_changed():
    original = [{"run_id": "r", "event_index": 1, "真實損益": 5.0, "預估時長": None, "預估單位": "", "目標價": None, "停損價": None,
                 "備註": ""},
                {"run_id": "r", "event_index": 2, "真實損益": 6.0, "預估時長": 4, "預估單位": "小時", "目標價": 2600.0,
                 "停損價": None, "備註": "a"}]
    edited = [{"預估時長": float("nan"), "預估單位": "", "目標價": float("nan"), "停損價": float("nan"), "備註": "",
               "真實損益": 5.0},  # NaN = 空白,沒改
              {"預估時長": 4, "預估單位": "天", "目標價": 2600.0, "停損價": float("nan"), "備註": "a", "真實損益": 6.0}]
    assert actions.pnl_changes(original, edited) == [
        {"run_id": "r", "event_index": 2, "預估時長": 4, "預估單位": "天", "目標價": 2600.0, "停損價": None, "備註": "a"}]


def test_pnl_changes_match_rows_by_key_not_position():
    """AG Grid 回傳的是篩選 / 排序後的列(2026-10-14):要用 run_id + event_index 對回原本那一列"""
    blank = {"預估時長": None, "預估單位": "", "目標價": None, "停損價": None, "備註": ""}
    original = [{"run_id": "r", "event_index": 1, **blank}, {"run_id": "r", "event_index": 2, **blank},
                {"run_id": "s", "event_index": 1, **blank}]
    edited = [{"run_id": "s", "event_index": 1, **blank, "目標價": 10.0},  # 排序後在前
              {"run_id": "r", "event_index": 1, **blank}]  # r/2 被篩掉
    assert actions.pnl_changes(original, edited) == [
        {"run_id": "s", "event_index": 1, "預估時長": None, "預估單位": None, "目標價": 10.0, "停損價": None, "備註": None}]


def test_pnl_hold_number_and_unit():
    """預估時長 = 數字 + 單位下拉(2026-10-14):90 分鐘、1.5 天;沒選單位當小時;數字清空 = 不填"""
    from strategy_lab.ui.data import hold_seconds

    assert (hold_seconds(90, "分鐘"), hold_seconds(1.5, "天"), hold_seconds(30, "秒")) == (5400, 129600, 30)
    assert hold_seconds(4, "") == 4 * 3600 and hold_seconds(None, "小時") is None
    assert hold_seconds(float("nan"), "小時") is None


def test_save_sat_notes_writes_sl_run_note(tmp_path):
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from strategy_lab.storage.models import SlRun
    from strategy_lab.storage.recorder import RunInfo, TradeRecorder
    from strategy_lab.storage.setup_db import setup

    url = f"sqlite:///{tmp_path / 'trading.db'}"
    setup(url)
    rec = TradeRecorder(db_url=url, pending_dir=tmp_path / "p", fetch_executions=lambda *a: [])
    run_id = rec.start_run(RunInfo(
        strategy_name="sat_strategy", strategy_path="NA", strategy_yaml="NA", strategy_params={}, config={},
        symbol="BTCUSDT", category="linear", direction="long", origin_price=1.0, origin_source="x", qty=None,
        order_type="limit", loop=None, testnet=False, preflight=None, log_path=None,
        started_at=datetime(2026, 10, 17, tzinfo=timezone.utc), account="test1"))
    assert actions.save_sat_notes(url, [{"run_id": run_id, "備註": " 大跌沒成交 "}]) == 1
    with Session(create_engine(url)) as s:
        assert s.get(SlRun, run_id).note == "大跌沒成交"
    with pytest.raises(ValueError, match="找不到"):
        actions.save_sat_notes(url, [{"run_id": "nope", "備註": "x"}])
