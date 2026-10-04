"""四張表的 schema。金額用 Numeric(20, 8)、時間用 timestamptz,跟
Fa_Successful_trade 的 bybit_wallet_snapshot 同一套慣例。主鍵都是自然鍵
(run_id / Bybit orderId / Bybit execId / run_id+event_index),寫入一律
upsert,重送(例如 backfill 重跑)不會產生重複資料。"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Integer, Numeric, String, Text
from sqlalchemy.orm import declarative_base
from sqlalchemy.types import TIMESTAMP

Base = declarative_base()

MONEY = Numeric(20, 8, asdecimal=False)
TS = TIMESTAMP(timezone=True)


class SlRun(Base):
    __tablename__ = "sl_run"

    run_id = Column(String(36), primary_key=True)
    strategy_name = Column(String(100), nullable=False)
    strategy_path = Column(String(300), nullable=False)
    strategy_yaml = Column(Text, nullable=False)
    strategy_params = Column(JSON, nullable=False)
    config = Column(JSON, nullable=False)  # 執行設定快照,不含任何憑證
    symbol = Column(String(30), nullable=False)
    category = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)
    origin_price = Column(MONEY)
    origin_source = Column(String(30))
    qty = Column(MONEY)
    order_type = Column(String(10), nullable=False)
    loop = Column(Integer)  # None = 不限次數
    testnet = Column(Boolean, nullable=False)
    preflight = Column(JSON)  # 啟動前 preflight 顯示並確認過的估算
    python_executable = Column(String(300))
    python_version = Column(String(30))
    git_commit = Column(String(60))
    log_path = Column(String(300))
    started_at = Column(TS, nullable=False)
    ended_at = Column(TS)
    end_reason = Column(String(30))  # window_cleanup / stop_requested / loop_done / kill_switch / crash / refused_leftover
    events_count = Column(Integer)
    gross_pnl = Column(MONEY)
    net_pnl = Column(MONEY)
    max_drawdown = Column(MONEY)


class SlOrder(Base):
    __tablename__ = "sl_order"

    order_id = Column(String(64), primary_key=True)  # Bybit orderId
    run_id = Column(String(36), nullable=False, index=True)
    event_index = Column(Integer, nullable=False)
    purpose = Column(String(20), nullable=False)  # entry / exit / forced_close
    side = Column(String(4), nullable=False)
    order_type = Column(String(10), nullable=False)
    price = Column(MONEY)  # 市價單為 NULL
    qty = Column(MONEY, nullable=False)
    reduce_only = Column(Boolean, nullable=False)
    status = Column(String(10), nullable=False)  # open / closed / canceled
    avg_price = Column(MONEY)
    filled_qty = Column(MONEY)
    created_at = Column(TS, nullable=False)
    updated_at = Column(TS, nullable=False)


class SlFill(Base):
    __tablename__ = "sl_fill"

    exec_id = Column(String(64), primary_key=True)  # Bybit execId
    run_id = Column(String(36), nullable=False, index=True)
    order_id = Column(String(64))
    event_index = Column(Integer)
    exec_type = Column(String(20), nullable=False)  # Trade / Funding
    side = Column(String(4))
    price = Column(MONEY)
    qty = Column(MONEY)
    fee = Column(MONEY)  # 正數 = 付出,負數 = 收到
    is_maker = Column(Boolean)
    exec_time = Column(TS, nullable=False)


class SlEvent(Base):
    __tablename__ = "sl_event"

    run_id = Column(String(36), primary_key=True)
    event_index = Column(Integer, primary_key=True)
    direction = Column(String(10), nullable=False)
    start_time = Column(TS, nullable=False)
    end_time = Column(TS, nullable=False)
    fills = Column(Integer, nullable=False)
    max_position = Column(MONEY, nullable=False)
    avg_entry = Column(MONEY)
    avg_exit = Column(MONEY)
    realized_pnl = Column(MONEY, nullable=False)  # 價差毛利
    fees = Column(MONEY, nullable=False)
    funding = Column(MONEY, nullable=False)
    net_pnl = Column(MONEY, nullable=False)  # = realized_pnl - fees - funding
    max_drawdown = Column(MONEY, nullable=False)  # <= 0
    forced = Column(Boolean, nullable=False)


MODELS = {m.__tablename__: m for m in (SlRun, SlOrder, SlFill, SlEvent)}
