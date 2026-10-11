"""多個 Bybit 帳戶(2026-10-12):strategy_lab/live/accounts.py 依帳戶名稱從 .env 找 key。

使用者的 .env 寫法(照使用者的格式,程式配合):
    ACCOUNT = test1                 ← 標題(給人看的;程式也用它判斷顯示名稱、檢查有沒有貼錯位置)
    BYBIT_API_KEY=...               ← 原本那組(sat_strategy / Fa / 舊程式還在用)= test1
    BYBIT_test1_API_KEY=            ← 留空 = 用上面原本那組
    BYBIT_test1_UID=...
    ACCOUNT = MAIN
    BYBIT_MAIN_API_KEY=...
名稱不分大小寫;沒指定帳戶 = test1;MAIN 找不到 key 就報錯,絕不改用別的帳戶。
測試全部用假的 key,不讀真的 .env。"""

import os

import pytest

from strategy_lab.live import accounts

ENV = """BYBIT_API_KEY=legacykey000000000
BYBIT_API_SECRET=legacysecret0000000000000000000000000

# sub(子帳戶,現有的,正在跑 SUI,不要動)
ACCOUNT = test1
BYBIT_test1_API_KEY=
BYBIT_test1_API_SECRET=
BYBIT_test1_UID=11111111

# main(帳戶)
ACCOUNT = MAIN
BYBIT_MAIN_API_KEY=mainkey0000000000
BYBIT_MAIN_API_SECRET=mainsecret00000000000000000000000000
BYBIT_MAIN_UID=22222222
"""


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV, encoding="utf-8")
    return path


class TestResolve:
    def test_main_by_any_case(self, env_file):
        for name in ("MAIN", "main", "Main"):
            acc = accounts.resolve(name, env_file)
            assert (acc.name, acc.api_key, acc.api_secret, acc.uid) == (
                "MAIN", "mainkey0000000000", "mainsecret00000000000000000000000000", "22222222")

    def test_test1_with_empty_own_key_falls_back_to_the_original_key(self, env_file):
        acc = accounts.resolve("TEST1", env_file)
        assert acc.name == "test1"  # 照 ACCOUNT = test1 的寫法顯示
        assert acc.api_key == "legacykey000000000" and acc.api_secret.startswith("legacysecret")
        assert acc.uid == "11111111"

    def test_no_account_means_test1(self, env_file):
        assert accounts.resolve(None, env_file).name == "test1"

    def test_own_key_wins_over_the_original(self, env_file):
        env_file.write_text(ENV.replace("BYBIT_test1_API_KEY=\nBYBIT_test1_API_SECRET=",
                                        "BYBIT_test1_API_KEY=ownkey\nBYBIT_test1_API_SECRET=ownsecret"), encoding="utf-8")
        acc = accounts.resolve("test1", env_file)
        assert (acc.api_key, acc.api_secret) == ("ownkey", "ownsecret")

    def test_missing_key_is_an_error_never_another_account(self, env_file):
        env_file.write_text(ENV.replace("BYBIT_MAIN_API_KEY=mainkey0000000000", "BYBIT_MAIN_API_KEY="), encoding="utf-8")
        with pytest.raises(accounts.AccountError, match="BYBIT_MAIN_API_KEY"):
            accounts.resolve("MAIN", env_file)

    def test_unknown_account(self, env_file):
        with pytest.raises(accounts.AccountError, match="foo"):
            accounts.resolve("foo", env_file)

    def test_secret_is_never_shown(self, env_file):
        acc = accounts.resolve("MAIN", env_file)
        assert "mainsecret" not in repr(acc) and "mainsecret" not in str(acc)
        assert "mainkey0000000000" not in repr(acc)

    def test_does_not_touch_os_environ(self, env_file):
        before = dict(os.environ)
        accounts.resolve("MAIN", env_file)
        assert dict(os.environ) == before


class TestListAndCheck:
    def test_lists_accounts_in_file_order_with_their_written_names(self, env_file):
        assert accounts.list_accounts(env_file) == ["test1", "MAIN"]

    def test_clean_file_has_no_warnings(self, env_file):
        assert accounts.check_layout(env_file) == []

    def test_key_under_the_wrong_heading_is_flagged(self, env_file):
        env_file.write_text(ENV + "BYBIT_test1_UID=33333333\n", encoding="utf-8")  # 寫在 ACCOUNT = MAIN 底下
        [warning] = accounts.check_layout(env_file)
        assert "BYBIT_test1_UID" in warning and "MAIN" in warning


def test_account_without_a_heading_is_listed_and_resolved_by_its_key_name(tmp_path):
    env = tmp_path / ".env"
    env.write_text(ENV + "\nBYBIT_Scalp_API_KEY=k\nBYBIT_Scalp_API_SECRET=s\n", encoding="utf-8")
    assert accounts.list_accounts(env) == ["test1", "MAIN", "Scalp"]
    assert accounts.resolve("scalp", env).name == "Scalp"


class TestVerify:
    """啟動前核對:key 真的屬於這個帳戶、能下單、沒過期;有提幣權限 / 快過期只警告。"""

    from datetime import datetime, timezone

    NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
    MAIN = accounts.AccountCredentials(name="MAIN", api_key="k", api_secret="s", uid="22222222")
    OK = {"uid": "22222222", "read_only": False, "can_withdraw": False, "expires_at": "2027-06-01T00:00:00Z"}

    def test_matching_key_passes(self):
        assert accounts.verify(self.MAIN, self.OK, self.NOW) == ([], [])

    def test_uid_mismatch_is_an_error(self):
        errors, _ = accounts.verify(self.MAIN, {**self.OK, "uid": "11111111"}, self.NOW)
        assert errors and "11111111" in errors[0] and "22222222" in errors[0] and "MAIN" in errors[0]

    def test_read_only_key_cannot_trade(self):
        errors, _ = accounts.verify(self.MAIN, {**self.OK, "read_only": True}, self.NOW)
        assert errors and "唯讀" in errors[0]

    def test_expired_key_is_an_error_and_soon_expiring_a_warning(self):
        errors, _ = accounts.verify(self.MAIN, {**self.OK, "expires_at": "2026-10-11T00:00:00Z"}, self.NOW)
        assert errors and "過期" in errors[0]
        errors, warnings = accounts.verify(self.MAIN, {**self.OK, "expires_at": "2026-10-15T00:00:00Z"}, self.NOW)
        assert errors == [] and warnings and "到期" in warnings[0]

    def test_withdraw_permission_is_a_warning(self):
        errors, warnings = accounts.verify(self.MAIN, {**self.OK, "can_withdraw": True}, self.NOW)
        assert errors == [] and warnings and "提幣" in warnings[0]

    def test_no_uid_configured_is_a_warning_with_the_real_uid(self):
        acc = accounts.AccountCredentials(name="test1", api_key="k", api_secret="s", uid=None)
        errors, warnings = accounts.verify(acc, self.OK, self.NOW)
        assert errors == [] and warnings and "UID" in warnings[0] and "22222222" in warnings[0]


def test_default_env_path_is_looked_up_at_call_time(env_file, monkeypatch):
    """conftest 把 accounts.ENV_PATH 換成假的 .env,測試就不會讀到真的 key"""
    monkeypatch.setattr(accounts, "ENV_PATH", env_file)
    assert accounts.resolve("MAIN").api_key == "mainkey0000000000"
    assert accounts.list_accounts() == ["test1", "MAIN"]


def test_tests_never_read_the_real_env_file():
    assert accounts.ENV_PATH != accounts.PROJECT_ROOT / ".env"
