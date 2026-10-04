"""在 TRADING_DB_URL 指向的資料庫建立 sl_run / sl_order / sl_fill / sl_event
(已存在就略過)。資料庫是 Fa_Successful_trade docker-compose.yml 的
`trading-postgres` 容器(port 5434,跟 Superset 的 Postgres 完全分開),
容器第一次啟動時就會建好 `trading` 資料庫:

    cd /Users/mac/fa_trade/Fa_Successful_trade && docker compose up -d trading-postgres

    /opt/anaconda3/bin/python3 -m strategy_lab.storage.setup_db
"""

from __future__ import annotations

import argparse
import os
from typing import List, Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect

from strategy_lab.storage.models import Base


def setup(db_url: str) -> List[str]:
    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)
    return sorted(t for t in inspect(engine).get_table_names() if t.startswith("sl_"))


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="建立 strategy_lab 的交易紀錄資料表")
    parser.add_argument("--db-url", default=os.getenv("TRADING_DB_URL"))
    args = parser.parse_args(argv)
    if not args.db_url:
        print("沒有 TRADING_DB_URL(.env)也沒有 --db-url")
        return 1
    print("資料表:", ", ".join(setup(args.db_url)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
