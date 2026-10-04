"""live/preflight.py:啟動前顯示估算,輸入確認才用 daemon 在背景啟動。
用假的交易所 client,不碰網路、不下單。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from strategy_lab.live import preflight

HKT = ZoneInfo("Asia/Hong_Kong")


@pytest.fixture(autouse=True)
def _snapshot_dir_in_tmp(tmp_path, monkeypatch):
    # 確認啟動時會寫 run/<設定檔>.preflight.json;測試寫到 tmp,不碰專案的 run/。
    monkeypatch.setattr(preflight, "SNAPSHOT_DIR", tmp_path / "run")


class FakeClient:
    def __init__(self, price=84650.0, equity=59.5, open_orders=None, position=0.0):
        self.price, self.equity = price, equity
        self.open_orders, self.position = open_orders or [], position

    def get_last_price(self, symbol):
        return self.price

    def get_account_equity(self):
        return self.equity

    def get_fee_rates(self, symbol):
        return 0.0002, 0.00055

    def get_leverage(self, symbol):
        return 100.0

    def get_margin_mode(self):
        return "REGULAR_MARGIN"

    def get_open_orders(self, symbol):
        return self.open_orders

    def get_position_qty(self, symbol):
        return self.position


def write_config(tmp_path, origin="84967.5", dry_run="false", sizing="fixed_qty", value="0.001"):
    path = tmp_path / "live_btc_band.yaml"
    path.write_text(
        "strategy_path: strategies/weekend_band_reversion.yaml\n"
        "symbol_override: BTCUSDT\n"
        f"origin_price: {origin}\n"
        f"dry_run: {dry_run}\ntestnet: false\n"
        f"position_sizing:\n  mode: {sizing}\n  value: {value}\n"
    )
    return path


def run(tmp_path, client, answer, monkeypatch, config=None):
    config = config or write_config(tmp_path)
    printed, started = [], []
    monkeypatch.setattr(preflight, "load_dotenv", lambda: None)
    code = preflight.main(
        ["--config", str(config)],
        client_factory=lambda cfg: client,
        input_fn=lambda prompt: answer,
        print_fn=lambda *a: printed.append(" ".join(str(x) for x in a)),
        start_fn=lambda path: started.append(path) or type("I", (), {"pid": 4242, "console_log": "x.log"})(),
        now_fn=lambda: datetime(2026, 10, 3, 12, 0, tzinfo=HKT),
    )
    return code, "\n".join(printed), started


class TestPreflight:
    def test_shows_estimate_and_starts_only_after_typing_yes(self, tmp_path, monkeypatch):
        code, out, started = run(tmp_path, FakeClient(price=84950.0), "yes", monkeypatch)

        assert code == 0
        assert "每輪損益" in out and "風險" in out and "掛單計畫" in out
        assert "真實資金" in out
        assert len(started) == 1

    def test_anything_but_yes_does_not_start_real_trading(self, tmp_path, monkeypatch):
        code, out, started = run(tmp_path, FakeClient(price=84950.0), "y", monkeypatch)

        assert started == []
        assert "沒有啟動" in out

    def test_warns_when_stale_origin_would_fill_entry_immediately(self, tmp_path, monkeypatch):
        # origin 84967.5、價帶 0.1% → 買單 84882.5,現價 84650 → 一掛就吃單成交
        _, out, _ = run(tmp_path, FakeClient(price=84650.0), "no", monkeypatch)

        assert "會立刻吃單成交" in out

    def test_refuses_when_exchange_has_leftover_orders_or_position(self, tmp_path, monkeypatch):
        client = FakeClient(price=84950.0, position=0.001)
        code, out, started = run(tmp_path, client, "yes", monkeypatch)

        assert code == 1
        assert started == []
        assert "持倉" in out

    def test_refuses_when_qty_is_below_exchange_minimum(self, tmp_path, monkeypatch):
        config = write_config(tmp_path, sizing="fixed_quote_amount", value="30")
        code, out, started = run(tmp_path, FakeClient(price=84950.0), "yes", monkeypatch, config=config)

        assert code == 1
        assert started == []
        assert "最小" in out

    def test_dry_run_accepts_y(self, tmp_path, monkeypatch):
        config = write_config(tmp_path, dry_run="true")
        code, out, started = run(tmp_path, FakeClient(price=84950.0), "y", monkeypatch, config=config)

        assert len(started) == 1
        assert "DRY RUN" in out

    def test_missing_config_is_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preflight, "load_dotenv", lambda: None)
        with pytest.raises(FileNotFoundError):
            preflight.main(["--config", str(tmp_path / "typo.yaml")], client_factory=lambda cfg: FakeClient())


class TestPreflightSnapshotHandoff:
    def test_confirmed_preflight_saves_estimate_for_the_run_record(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preflight, "SNAPSHOT_DIR", tmp_path / "run")
        run(tmp_path, FakeClient(price=84950.0), "yes", monkeypatch)

        from strategy_lab.live.preflight import load_snapshot

        snap = load_snapshot(tmp_path / "live_btc_band.yaml", snapshot_dir=tmp_path / "run")
        assert snap["metrics"]["每輪損益(完成一輪才實現)"]["net_pnl"] is not None
        assert load_snapshot(tmp_path / "live_btc_band.yaml", snapshot_dir=tmp_path / "run") is None  # 讀一次就刪

    def test_cancelled_preflight_saves_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preflight, "SNAPSHOT_DIR", tmp_path / "run")
        run(tmp_path, FakeClient(price=84950.0), "no", monkeypatch)

        from strategy_lab.live.preflight import load_snapshot

        assert load_snapshot(tmp_path / "live_btc_band.yaml", snapshot_dir=tmp_path / "run") is None
