"""手動檢查 Telegram 分流(2026-10-11):從「故障位置」各發一則測試訊息到真的 bot,看實際長相。

    cd /Users/mac/strategy_lab && .venv/bin/python scripts/telegram_route_check.py

會送出 4 則(內容都標明【測試,請忽略】,不下單、不碰交易所):
    1. 交易快報  @Fa_trade_notice_bot:確定的成交失敗(模擬交易所拒絕平倉)
    2. 交易快報  @Fa_trade_notice_bot:🟢 成交
    3. 系統警報  @Fa_sys_alert_bot:🔴 錯誤(模擬資料庫寫入失敗)
    4. 原本的 bot @fa_strategy_lab_bot:⚫ 狀態
讀 strategy_lab/.env 的 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID、TELEGRAM_TRADE_* 、TELEGRAM_SYSTEM_*;
沒設定的 bot 會改送原本的 bot(印出來會標示)。不會印出任何 token。
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from strategy_lab.log.telegram_notifier import TelegramNotifier, telegram_filter  # noqa: E402


def _simulate_exchange_rejected_close() -> None:
    """故障位置 1:平倉被交易所拒絕 → 交易快報"""
    logger.bind(trade=True).error("【測試,請忽略】市價平倉被交易所拒絕:ab not enough for new order(模擬)")


def _simulate_fill() -> None:
    logger.bind(telegram=True, category="fill").info("【測試,請忽略】✅ 成交|平倉(第1注)Sell 20 @ 1.2322(模擬)")


def _simulate_db_write_failure() -> None:
    """故障位置 2:資料庫寫不進去 → 系統警報"""
    logger.error("【測試,請忽略】寫入交易資料庫失敗:the database system is in recovery mode(模擬)")


def _simulate_status() -> None:
    logger.bind(telegram=True, category="status").info("【測試,請忽略】⏱ strategy_lab 運作中(模擬的每小時狀態)")


def main() -> int:
    notifier = TelegramNotifier(project="Strategy Lab")
    print(f"原本的 bot:{'有設定' if notifier.enabled else '沒設定'}")
    print(f"交易快報:{'有設定' if notifier.trade_enabled else '沒設定 → 改送原本的 bot'}")
    print(f"系統警報:{'有設定' if notifier.system_enabled else '沒設定 → 改送原本的 bot'}")
    if not notifier.enabled and not notifier.trade_enabled and not notifier.system_enabled:
        print("一個 bot 都沒設定,不發送")
        return 1
    logger.remove()
    logger.add(sys.stdout, level="INFO")
    logger.add(notifier.sink, level="INFO", filter=telegram_filter)
    for fn in (_simulate_exchange_rejected_close, _simulate_fill, _simulate_db_write_failure, _simulate_status):
        fn()
    ok = notifier.flush(timeout=30)
    print(f"送出完成:{ok},沒送出的 {notifier.dropped} 則")
    return 0 if ok and notifier.dropped == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
