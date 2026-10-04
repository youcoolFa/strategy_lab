"""交易紀錄寫進資料庫(Postgres 的 `trading` 資料庫,`TRADING_DB_URL`)。

四張表都用 `sl_` 前綴(strategy_lab;另外 `order` 是 SQL 保留字):
    sl_run    每次啟動
    sl_order  每張單(進場 / 平倉 / 強制平倉)
    sl_fill   每筆成交與資金費(來自 Bybit 成交明細,手續費以交易所為準)
    sl_event  每個 event(部位 0 → 0),含淨損益與期間最大回撤

dry-run 不寫。資料庫掛掉不影響交易:第一次寫入失敗後,這次執行剩下的紀錄
都改寫 `logs/db_pending/*.jsonl`,之後用 `python -m strategy_lab.storage.backfill`
補進資料庫。建表:`python -m strategy_lab.storage.setup_db`。
"""
