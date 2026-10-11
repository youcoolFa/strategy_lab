"""全部測試共用:確保測試絕對不會用 .env 裡的真 token 發 Telegram 訊息到手機。

要在「模組層級」就清空:live/main.py 的 main() 會 load_dotenv(),而 load_dotenv()
不會覆蓋已經存在的環境變數(即使是空字串),所以先設成空字串就擋住了。"""

import os

import pytest

os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
os.environ["TELEGRAM_TRADE_BOT_TOKEN"] = ""  # 交易快報 / 系統警報(2026-10-11)也不能真的發
os.environ["TELEGRAM_TRADE_CHAT_ID"] = ""
os.environ["TELEGRAM_SYSTEM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_SYSTEM_CHAT_ID"] = ""
# log 大小限制器在測試裡不清任何東西(有些測試會在真的 logs/ 設定 log);要測限制器的測試自己 monkeypatch。
os.environ["LOG_MAX_TOTAL_MB"] = "1000000"


@pytest.fixture(autouse=True)
def _no_real_telegram(monkeypatch):
    from strategy_lab.log import logger_setup

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("TELEGRAM_TRADE_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_TRADE_CHAT_ID", "")
    monkeypatch.setenv("TELEGRAM_SYSTEM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_SYSTEM_CHAT_ID", "")
    monkeypatch.setattr(logger_setup, "_notifier", None)
    yield


# 多帳戶(2026-10-12):live/accounts.py 預設讀專案的 .env(有真的 Bybit key)。測試一律換成假的 .env,
# 不會讀到、也不會用到真的 key。要在任何測試模組 import 前就換掉。
import tempfile as _tempfile  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from strategy_lab.live import accounts as _accounts  # noqa: E402

_fake_env = _Path(_tempfile.mkdtemp(prefix="sl_test_env_")) / ".env"
_fake_env.write_text("BYBIT_API_KEY=dummy-test-key\nBYBIT_API_SECRET=dummy-test-secret\n"
                     "ACCOUNT = test1\nBYBIT_test1_API_KEY=\nBYBIT_test1_API_SECRET=\n", encoding="utf-8")
_accounts.ENV_PATH = _fake_env

