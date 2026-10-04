"""把資料庫掛掉時寫在本機的紀錄(`logs/db_pending/*.jsonl`)補進資料庫。
寫入是 upsert,重跑不會重複;補完的檔案改名成 `.jsonl.done`。

    /opt/anaconda3/bin/python3 -m strategy_lab.storage.backfill
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from strategy_lab.storage.models import MODELS, Base
from strategy_lab.storage.recorder import PROJECT_ROOT, decode_row

DEFAULT_PENDING_DIR = PROJECT_ROOT / "logs" / "db_pending"


def backfill(db_url: str, pending_dir: Path = DEFAULT_PENDING_DIR) -> int:
    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)
    count = 0
    for path in sorted(Path(pending_dir).glob("*.jsonl")):
        with Session(engine) as session:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                session.merge(MODELS[rec["table"]](**decode_row(rec["table"], rec["data"])))
                count += 1
            session.commit()
        path.rename(path.with_name(path.name + ".done"))
    return count


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="把本機暫存的交易紀錄補進資料庫")
    parser.add_argument("--db-url", default=os.getenv("TRADING_DB_URL"))
    parser.add_argument("--pending-dir", default=str(DEFAULT_PENDING_DIR))
    args = parser.parse_args(argv)
    if not args.db_url:
        print("沒有 TRADING_DB_URL(.env)也沒有 --db-url")
        return 1
    print(f"已補進資料庫 {backfill(args.db_url, Path(args.pending_dir))} 筆")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
