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
        assert snap["metrics"]["每輪損益(依 level)"]["level_1"] is not None
        assert load_snapshot(tmp_path / "live_btc_band.yaml", snapshot_dir=tmp_path / "run") is None  # 讀一次就刪

    def test_cancelled_preflight_saves_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preflight, "SNAPSHOT_DIR", tmp_path / "run")
        run(tmp_path, FakeClient(price=84950.0), "no", monkeypatch)

        from strategy_lab.live.preflight import load_snapshot

        assert load_snapshot(tmp_path / "live_btc_band.yaml", snapshot_dir=tmp_path / "run") is None


def write_scale_config(tmp_path, entry_prices="[84900, 84800, 84700]", value="0.002"):
    path = tmp_path / "live_btc_scale.yaml"
    path.write_text(
        "strategy_path: strategies/scale_in_ladder.yaml\n"
        "symbol_override: BTCUSDT\n"
        + (f"entry_prices: {entry_prices}\n" if entry_prices else "")
        + "dry_run: false\ntestnet: false\n"
        f"position_sizing:\n  mode: fixed_qty\n  value: {value}\n"
    )
    return path


class TestScaleInPreflight:
    def test_shows_each_lot_and_level_pnl(self, tmp_path, monkeypatch):
        code, out, started = run(tmp_path, FakeClient(price=84950.0), "no", monkeypatch, config=write_scale_config(tmp_path))
        assert code == 0 and started == []
        assert "第1注" in out and "第2注" in out and "第3注" in out
        assert "level 3" in out

    def test_missing_entry_prices_is_refused(self, tmp_path, monkeypatch):
        code, out, started = run(tmp_path, FakeClient(price=84950.0), "yes", monkeypatch,
                                 config=write_scale_config(tmp_path, entry_prices=None))
        assert code == 1 and started == [] and "entry_prices" in out

    def test_lot_below_exchange_minimum_is_refused(self, tmp_path, monkeypatch):
        # 第一注 0.001 → 第三注 = 0.001 × 1/2 = 0.0005 < BTC 最小 0.001
        code, out, started = run(tmp_path, FakeClient(price=84950.0), "yes", monkeypatch,
                                 config=write_scale_config(tmp_path, value="0.001"))
        assert code == 1 and started == [] and "第3注" in out and "最小" in out


class TestOptionalLots:
    def test_disabled_third_lot_is_not_checked_against_minimum_qty(self, tmp_path, monkeypatch):
        # 第一注 0.001 → 第三注 0.0005 會低於最小量;但第三注填 0 = 不用,所以不擋
        code, out, started = run(tmp_path, FakeClient(price=84950.0), "no", monkeypatch,
                                 config=write_scale_config(tmp_path, entry_prices="[84900, 84800, 0]", value="0.001"))
        assert code == 0 and "第3注" not in out.split("【每輪損益")[0].split("【掛單計畫】")[1]
        assert "level 2" in out

    def test_third_without_second_is_refused(self, tmp_path, monkeypatch):
        code, out, started = run(tmp_path, FakeClient(price=84950.0), "yes", monkeypatch,
                                 config=write_scale_config(tmp_path, entry_prices="[84900, 0, 84700]"))
        assert code == 1 and started == [] and "有第二注才有第三注" in out


BAND_YAML = """name: band_example
symbol: BTC/USDT
direction: both
loop: null
scale_in: false
band: true
entry:
  type: band
  params: {buy_pct: 0.1, sell_pct: 0.2}
exit:
  type: band
  params: {}
time_window:
  type: weekly_window
  params: {end_weekday: 0, end_time: "06:00"}
"""


def write_band_config(tmp_path, origin="null", adopt="false"):
    strategy = tmp_path / "band.yaml"
    strategy.write_text(BAND_YAML)
    path = tmp_path / "live_band.yaml"
    path.write_text(f"strategy_path: {strategy}\nsymbol_override: BTCUSDT\norigin_price: {origin}\n"
                    "dry_run: false\ntestnet: false\nposition_sizing:\n  mode: fixed_qty\n  value: 0.001\n"
                    f"adopt_existing_position: {adopt}\n")
    return path


class TestBandPreflight:
    """區間策略(2026-10-10):預覽列出上下兩張單與規則;交易所上有殘留就拒絕(不支援接手)。"""

    def test_shows_both_orders_and_starts_after_yes(self, tmp_path, monkeypatch):
        code, out, started = run(tmp_path, FakeClient(price=100000.0), "yes", monkeypatch,
                                 config=write_band_config(tmp_path))
        assert code == 0 and len(started) == 1
        assert "買 99900" in out and "賣 100200" in out  # origin = 現價 100000
        assert "對面" in out and "每次穿過區間" in out and "多單或空單" in out

    def test_leftovers_are_refused_even_with_adopt(self, tmp_path, monkeypatch):
        client = FakeClient(price=100000.0, position=0.001)
        code, out, started = run(tmp_path, client, "yes", monkeypatch, config=write_band_config(tmp_path, adopt="true"))
        assert code == 1 and started == []
        assert "區間策略" in out and "接手" in out
