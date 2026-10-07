"""運作中改參數的終端機指令(live/control.py):解析、寫回設定檔、請求檔、背景程式套用、CLI 流程。
不碰網路(假 client)、不碰真的 run/、logs/、live_execution_config.yaml。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from strategy_lab.broker.paper_broker import PaperBroker
from strategy_lab.dsl.loader import load_strategy
from strategy_lab.engine.scale_in_runner import ScaleInRunner
from strategy_lab.live import control
from strategy_lab.live.config import ExecutionConfig, load_execution_config
from strategy_lab.live.main import apply_strategy_overrides
from strategy_lab.plugins.entry.scale_in import ScaleInEntry
from strategy_lab.plugins.exit.scale_out import ScaleOutExit
from strategy_lab.plugins.time_window.weekly_window import WeeklyWindow

NOW = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)

CONFIG_TEXT = """# 執行設定
strategy_path: strategies/scale_in_ladder.yaml
symbol_override: SUIUSDT
entry_prices: [1.2297, 1.2291, 1.2285]  # 2026-10-06 SUI 現價 1.2309
dry_run: false   # 真實下單開關
testnet: false
position_sizing:
  mode: fixed_qty
  value: 20   # 第一注 20 SUI
use_live_ticker_feed: false
"""


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    # main() 會 load_dotenv();測試不能把真的 .env(REDIS_URL 等)帶進環境變數,影響其他測試
    monkeypatch.setattr(control, "load_dotenv", lambda *a, **k: None)


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "live_sui.yaml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    return path


class TestParse:
    def test_values(self):
        current = {"value": 0.2, "unit": "pct"}
        assert control.parse_assignments(["entry_prices=1.22,1.219,1.218"], current) == {
            "entry_prices": [1.22, 1.219, 1.218]}
        assert control.parse_assignments(["distance=0.3"], current) == {"distance": {"value": 0.3, "unit": "pct"}}
        assert control.parse_assignments(["distance=500points"], current) == {"distance": {"value": 500.0, "unit": "points"}}
        assert control.parse_assignments(["loop=3"], current) == {"loop": 3}
        assert control.parse_assignments(["loop=null"], current) == {"loop": None}

    @pytest.mark.parametrize("bad", ["origin_price=1", "loop=-1", "loop=abc", "distance=abc", "entry_prices=", "nonsense"])
    def test_rejects_bad_input(self, bad):
        with pytest.raises(ValueError):
            control.parse_assignments([bad], {"value": 0.2, "unit": "pct"})


class TestWriteBack:
    def test_entry_prices_line_and_overrides_block_keep_other_lines_and_comments(self, config_path):
        control.write_back(config_path, {"entry_prices": [1.22, 1.219, 1.218], "loop": 3,
                                         "distance": {"value": 0.3, "unit": "pct"}})
        text = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        assert data["entry_prices"] == [1.22, 1.219, 1.218]
        assert data["strategy_overrides"] == {"loop": 3, "exit_distance": {"value": 0.3, "unit": "pct"}}
        assert "# 2026-10-06 SUI 現價 1.2309" in text  # 原本那一行的註解保留
        assert "# 真實下單開關" in text and "# 第一注 20 SUI" in text
        assert data["dry_run"] is False and data["position_sizing"]["value"] == 20

    def test_overrides_are_merged_and_rewritten_in_place(self, config_path):
        control.write_back(config_path, {"loop": 3})
        control.write_back(config_path, {"distance": {"value": 0.5, "unit": "pct"}})
        control.write_back(config_path, {"loop": None})
        text = config_path.read_text(encoding="utf-8")
        assert text.count("strategy_overrides:") == 1
        assert yaml.safe_load(text)["strategy_overrides"] == {"loop": None, "exit_distance": {"value": 0.5, "unit": "pct"}}


class TestOverridesAreUsedOnStartup:
    def test_loaded_config_overrides_strategy_loop_and_distance(self, config_path):
        control.write_back(config_path, {"loop": 5, "distance": {"value": 0.4, "unit": "pct"}})
        config = load_execution_config(config_path=config_path)
        strategy = apply_strategy_overrides(load_strategy("strategies/scale_in_ladder.yaml"), config)
        assert strategy.loop == 5
        assert isinstance(strategy.exit, ScaleOutExit) and strategy.exit.value == 0.4

    def test_distance_override_on_a_non_scale_in_strategy_is_an_error(self):
        config = ExecutionConfig(strategy_overrides={"exit_distance": {"value": 0.4, "unit": "pct"}})
        with pytest.raises(ValueError, match="分注"):
            apply_strategy_overrides(load_strategy("strategies/weekend_mean_reversion.yaml"), config)


def scale_runner():
    runner = ScaleInRunner(
        entry=ScaleInEntry(weights=[2, 3, 1]), exit=ScaleOutExit(distance={"value": 0.2, "unit": "pct"}),
        time_window=WeeklyWindow(end_weekday=0, end_time="06:00", cleanup_buffer_minutes=5),
        order_qty=20.0, entry_prices=[1.2297, 1.2291, 1.2285], loop=2, broker=PaperBroker(),
    )
    runner.start(NOW, 1.2309)
    runner.tick(NOW, 1.2309)
    return runner


class TestDaemonSide:
    def test_request_is_applied_written_back_and_answered(self, config_path, tmp_path):
        run_dir = tmp_path / "run"
        runner = scale_runner()
        control.write_request(config_path, {"entry_prices": [1.2300, 1.2294, 1.2288], "loop": 3}, run_dir)

        assert control.process_control(runner, config_path, NOW + timedelta(minutes=1), 1.2309, run_dir) is True
        assert runner.entry_prices == [1.23, 1.2294, 1.2288] and runner.loop == 3
        result = control.read_result(config_path, run_dir)
        assert result["ok"] is True and any("1.23" in m for m in result["messages"])
        assert yaml.safe_load(config_path.read_text())["strategy_overrides"]["loop"] == 3
        assert control.process_control(runner, config_path, NOW, 1.2309, run_dir) is False  # 請求已處理掉

    def test_rejected_request_changes_nothing(self, config_path, tmp_path):
        run_dir = tmp_path / "run"
        runner = scale_runner()
        before = config_path.read_text()
        control.write_request(config_path, {"entry_prices": [1.2400, 1.2294, 1.2288]}, run_dir)  # 越過現價

        control.process_control(runner, config_path, NOW, 1.2309, run_dir)
        result = control.read_result(config_path, run_dir)
        assert result["ok"] is False and "越過現價" in result["error"]
        assert runner.entry_prices == [1.2297, 1.2291, 1.2285]
        assert config_path.read_text() == before


class FakeClient:
    def get_last_price(self, symbol):
        return 1.2309

    def get_fee_rates(self, symbol):
        return 0.0002, 0.00055


class TestCli:
    def run_cli(self, config_path, tmp_path, args, answer="yes", running=False, wait_fn=None):
        printed = []
        code = control.main(
            ["--config", str(config_path), "set", *args],
            client_factory=lambda cfg: FakeClient(), input_fn=lambda prompt: answer,
            print_fn=lambda *a: printed.append(" ".join(str(x) for x in a)),
            run_dir=tmp_path / "run", is_running_fn=lambda path, run_dir: running,
            wait_fn=wait_fn or (lambda path, run_dir, timeout: None),
        )
        return code, "\n".join(printed)

    def test_preview_shows_old_and_new_and_cancel_keeps_everything(self, config_path, tmp_path):
        before = config_path.read_text()
        code, out = self.run_cli(config_path, tmp_path, ["entry_prices=1.2300,1.2294,1.2288", "distance=0.3"], answer="no")
        assert code == 0 and "已取消" in out
        assert "1.2297 → 1.23" in out and "0.2pct → 0.3pct" in out
        assert "第1注" in out and "平倉" in out  # 每注新價格預覽
        assert config_path.read_text() == before

    def test_not_running_writes_config_directly(self, config_path, tmp_path):
        code, out = self.run_cli(config_path, tmp_path, ["loop=4"])
        assert code == 0 and "沒有在跑" in out
        assert yaml.safe_load(config_path.read_text())["strategy_overrides"]["loop"] == 4
        assert not (tmp_path / "run").exists() or not list((tmp_path / "run").glob("*.control.json"))

    def test_running_sends_request_and_prints_result(self, config_path, tmp_path):
        def fake_daemon(path, run_dir, timeout):
            assert control.read_request(path, run_dir) == {"loop": 4}
            return {"ok": True, "messages": ["loop 2 → 4(共 3 個 → 共 5 個)"]}

        code, out = self.run_cli(config_path, tmp_path, ["loop=4"], running=True, wait_fn=fake_daemon)
        assert code == 0 and "已套用" in out and "共 5 個" in out

    def test_running_but_rejected_reports_reason(self, config_path, tmp_path):
        code, out = self.run_cli(config_path, tmp_path, ["loop=4"], running=True,
                                 wait_fn=lambda p, r, t: {"ok": False, "error": "已完成 5 個 loop"})
        assert code == 1 and "被拒絕" in out and "已完成 5 個 loop" in out

    def test_crossing_price_is_warned_in_preview(self, config_path, tmp_path):
        code, out = self.run_cli(config_path, tmp_path, ["entry_prices=1.2400,1.2294,1.2288"], answer="no")
        assert "⚠" in out and "越過現價" in out


class TestRunForeverAppliesRequests:
    def test_request_written_while_running_is_applied_between_ticks(self, tmp_path, monkeypatch):
        import strategy_lab.live.main as main_module
        from loguru import logger

        from strategy_lab.live.main import build_runner_and_symbol, run_forever
        from strategy_lab.log.telegram_notifier import telegram_filter

        monkeypatch.setenv("BYBIT_API_KEY", "dummy")
        monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
        config_file = tmp_path / "live_wmr.yaml"
        config_file.write_text("strategy_path: strategies/weekend_mean_reversion.yaml\ndry_run: true\n", encoding="utf-8")
        config = load_execution_config(config_path=config_file)
        config.poll_interval_seconds = 0
        runner, symbol = build_runner_and_symbol(config)
        run_dir = tmp_path / "run"
        clock = {"now": NOW, "ticks": 0}

        def fake_get_price(runner, config, symbol):
            clock["now"] += timedelta(minutes=5)
            clock["ticks"] += 1
            if clock["ticks"] == 2:
                control.write_request(config_file, {"loop": 7}, run_dir)  # 運作中送出變更
            if clock["ticks"] > 4:
                runner.request_stop()
            return 1000.0

        sent = []
        sink = logger.add(lambda m: sent.append(m.record["message"]), level="DEBUG", filter=telegram_filter)
        monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
        monkeypatch.setattr(main_module, "get_current_price", fake_get_price)
        try:
            run_forever(runner, config, symbol, now_fn=lambda: clock["now"], config_path=config_file, control_run_dir=run_dir)
        finally:
            logger.remove(sink)

        assert runner.loop == 7
        assert control.read_result(config_file, run_dir)["ok"] is True
        assert yaml.safe_load(config_file.read_text())["strategy_overrides"]["loop"] == 7
        assert any("🔧 參數已更新" in m for m in sent)



class TestStaleAndTimeout:
    def test_old_request_is_not_applied(self, config_path, tmp_path, monkeypatch):
        run_dir = tmp_path / "run"
        runner = scale_runner()
        control.write_request(config_path, {"loop": 9}, run_dir)
        later = control.time.time() + 3600
        monkeypatch.setattr(control.time, "time", lambda: later)  # 一小時後才讀到
        control.process_control(runner, config_path, NOW, 1.2309, run_dir)
        assert runner.loop == 2
        assert "過期" in control.read_result(config_path, run_dir)["error"]

    def test_timeout_withdraws_the_request(self, config_path, tmp_path):
        code, out = TestCli().run_cli(config_path, tmp_path, ["loop=4"], running=True, wait_fn=lambda p, r, t: None)
        assert code == 1 and "已撤回" in out
        assert control.read_request(config_path, tmp_path / "run") is None


class TestPreviewWarnings:
    def test_changing_only_distance_does_not_warn_about_entry_prices(self, config_path, tmp_path):
        class LowPrice(FakeClient):
            def get_last_price(self, symbol):
                return 1.10  # 現價遠低於建倉價(都已成交的情況)

        printed = []
        control.main(["--config", str(config_path), "set", "distance=0.3"], client_factory=lambda c: LowPrice(),
                     input_fn=lambda p: "no", print_fn=lambda *a: printed.append(" ".join(map(str, a))),
                     run_dir=tmp_path / "run", is_running_fn=lambda p, r: True, wait_fn=lambda p, r, t: None)
        assert "越過現價" not in "\n".join(printed)


class TestOptionalLotsCli:
    def test_zero_lot_is_shown_as_not_used_and_bad_pattern_refused(self, config_path, tmp_path):
        code, out = TestCli().run_cli(config_path, tmp_path, ["entry_prices=1.2300,1.2294,0"], answer="no")
        assert code == 0 and "第3注 不使用" in out
        code, out = TestCli().run_cli(config_path, tmp_path, ["entry_prices=1.2300,0,1.2288"], answer="no")
        assert code == 1 and "有第二注才有第三注" in out
