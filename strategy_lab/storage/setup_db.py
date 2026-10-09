"""在 TRADING_DB_URL 指向的資料庫建立 sl_run / sl_order / sl_fill / sl_event
(已存在就略過)。資料庫是 Fa_Successful_trade docker-compose.yml 的
`trading-postgres` 容器(port 5434,跟 Superset 的 Postgres 完全分開),
容器第一次啟動時就會建好 `trading` 資料庫:

    cd /Users/mac/fa_trade/Fa_Successful_trade && docker compose up -d trading-postgres

    .venv/bin/python -m strategy_lab.storage.setup_db
"""

from __future__ import annotations

import argparse
import os
from typing import List, Optional

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, inspect, text

from strategy_lab.storage.models import Base


def setup(db_url: str) -> List[str]:
    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    _apply_comments(engine)
    tables = sorted(t for t in inspect(engine).get_table_names() if t.startswith("sl_"))
    engine.dispose()
    return tables


def _quote(text_: str) -> str:
    return "'" + text_.replace("'", "''") + "'"


def comment_statements(metadata: MetaData) -> List[str]:
    """把 models.py 的中文說明變成 COMMENT ON 語句。create_all 只在建表時帶 comment,
    已存在的表(或之後改了說明)要靠這些語句更新;重跑只是覆蓋成同樣內容。"""
    stmts: List[str] = []
    for table in metadata.sorted_tables:
        if table.comment:
            stmts.append(f"COMMENT ON TABLE {table.name} IS {_quote(table.comment)}")
        stmts += [f"COMMENT ON COLUMN {table.name}.{c.name} IS {_quote(c.comment)}" for c in table.columns if c.comment]
    return stmts


def _apply_comments(engine) -> None:
    if engine.dialect.name != "postgresql":  # SQLite 沒有 COMMENT ON(測試用)
        return
    # 用 exec_driver_sql 原樣送出:說明裡有「(USDT):正數」這種冒號,text() 會當成 :正數 參數;
    # psycopg2 會把 % 當格式符號,要寫成 %%
    with engine.begin() as conn:
        for stmt in comment_statements(Base.metadata):
            conn.exec_driver_sql(stmt.replace("%", "%%"))


def _add_missing_columns(engine) -> None:
    """create_all 不會幫已存在的表加新欄位(例:2026-10-04 加的 sl_order.lot)。
    只補可為 NULL 的新欄位,舊資料不用改;不刪、不改既有欄位。"""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or not column.nullable:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}"))


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
