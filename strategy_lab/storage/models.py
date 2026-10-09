"""四張表的 schema。金額用 Numeric(20, 8)、時間用 timestamptz,跟
Fa_Successful_trade 的 bybit_wallet_snapshot 同一套慣例。主鍵都是自然鍵
(run_id / Bybit orderId / Bybit execId / run_id+event_index),寫入一律
upsert,重送(例如 backfill 重跑)不會產生重複資料。

每張表、每個欄位的 comment 是給人看的中文說明:setup_db 會用 COMMENT ON 寫進
Postgres,在 Superset / 任何 DB 工具打開表就看得到(2026-10-09)。"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Integer, Numeric, String, Text
from sqlalchemy.orm import declarative_base
from sqlalchemy.types import TIMESTAMP

Base = declarative_base()

MONEY = Numeric(20, 8, asdecimal=False)
TS = TIMESTAMP(timezone=True)


class SlRun(Base):
    __tablename__ = "sl_run"
    __table_args__ = {"comment": "strategy_lab 每次啟動一列:用哪個策略、參數、執行設定、開始/結束時間、結束原因、總損益。"
                                 "被啟動檢查擋下(refused_leftover)或當掉(crash)也有一列"}

    run_id = Column(String(36), primary_key=True, comment="這次啟動的 ID(uuid);sl_order / sl_fill / sl_event 用它對回來")
    strategy_name = Column(String(100), nullable=False, comment="策略名稱,例 scale_in_ladder;free style = 使用者手動交易的紀錄(live/free_style.py)")
    strategy_path = Column(String(300), nullable=False, comment="策略 YAML 路徑,例 strategies/scale_in_ladder.yaml;free style 為 NA")
    strategy_yaml = Column(Text, nullable=False, comment="啟動當下策略 YAML 全文")
    strategy_params = Column(JSON, nullable=False, comment="策略參數(JSON):方向、loop、建倉/平倉規則、時間窗…")
    config = Column(JSON, nullable=False, comment="執行設定快照(JSON,live_execution_config.yaml):symbol、建倉價、dry_run、testnet、下單數量…;不含任何 key/密碼")
    symbol = Column(String(30), nullable=False, comment="交易對,Bybit 格式,例 SUIUSDT")
    category = Column(String(20), nullable=False, comment="Bybit 商品類型:linear = USDT 永續 / spot / inverse / option")
    direction = Column(String(10), nullable=False, comment="方向:long 做多 / short 做空")
    origin_price = Column(MONEY, comment="起始參考價(有些策略用來算建倉價);分注策略不用,為 NULL")
    origin_source = Column(String(30), comment="origin_price 的來源,例:手動輸入 / 啟動當下即時價")
    qty = Column(MONEY, comment="下單數量(分注策略是第一注的數量)")
    order_type = Column(String(10), nullable=False, comment="下單方式:limit 限價 / market 市價")
    loop = Column(Integer, comment="重複次數:總輪數 = loop + 1;NULL = 不限次數,做到時間窗結束")
    testnet = Column(Boolean, nullable=False, comment="true = Bybit 測試網;false = 正式站(真錢)")
    preflight = Column(JSON, comment="啟動前 preflight 顯示並經使用者確認的估算(JSON)")
    python_executable = Column(String(300), comment="執行用的 Python 路徑")
    python_version = Column(String(30), comment="Python 版本")
    git_commit = Column(String(60), comment="啟動當下的 git commit;結尾 -dirty = 有未 commit 的改動")
    log_path = Column(String(300), comment="這次執行的 log 檔路徑")
    started_at = Column(TS, nullable=False, comment="開始時間")
    ended_at = Column(TS, comment="結束時間;還在跑時為 NULL")
    end_reason = Column(String(30), comment="結束原因:window_cleanup 時間窗收尾 / stop_requested 手動停止 / loop_done 輪數做完 / "
                                            "kill_switch 風控停止 / crash 當掉 / refused_leftover 有殘留單被擋下沒啟動 / "
                                            "detached 脫離(持倉留給下一次接手)/ free_style_stop free style 停止")
    events_count = Column(Integer, comment="完成幾輪(部位 0 → 0 算一輪)")
    gross_pnl = Column(MONEY, comment="價差毛利總和(USDT,未扣手續費、資金費)")
    net_pnl = Column(MONEY, comment="淨利總和(USDT)= 毛利 − 手續費 − 資金費")
    max_drawdown = Column(MONEY, comment="整段執行的最大回撤(USDT,<= 0)")


class SlOrder(Base):
    __tablename__ = "sl_order"
    __table_args__ = {"comment": "每張單一列(建倉、平倉、收尾強制平倉);下單時寫入,成交或取消時更新狀態"}

    order_id = Column(String(64), primary_key=True, comment="Bybit orderId")
    run_id = Column(String(36), nullable=False, index=True, comment="屬於哪次啟動(對應 sl_run.run_id)")
    event_index = Column(Integer, nullable=False, comment="屬於第幾輪(1 起算,對應 sl_event.event_index)")
    purpose = Column(String(20), nullable=False, comment="用途:entry 建倉 / exit 平倉 / forced_close 收尾市價平倉 / manual 手動下的單(free style)")
    lot = Column(Integer, comment="分注策略的第幾注(1 起算);非分注策略為 NULL")
    side = Column(String(4), nullable=False, comment="買賣方向:Buy / Sell")
    order_type = Column(String(10), nullable=False, comment="limit 限價 / market 市價")
    price = Column(MONEY, comment="掛單價;市價單為 NULL")
    qty = Column(MONEY, nullable=False, comment="下單數量")
    reduce_only = Column(Boolean, nullable=False, comment="true = 只減倉(平倉單)")
    status = Column(String(10), nullable=False, comment="最後狀態:open 掛著 / closed 已成交 / canceled 已取消")
    avg_price = Column(MONEY, comment="成交均價;沒成交為 NULL")
    filled_qty = Column(MONEY, comment="已成交數量")
    created_at = Column(TS, nullable=False, comment="下單時間")
    updated_at = Column(TS, nullable=False, comment="最後一次狀態更新時間(成交 / 取消)")


class SlFill(Base):
    __tablename__ = "sl_fill"
    __table_args__ = {"comment": "Bybit 成交明細一筆一列(含資金費結算);每輪結束和收尾時從交易所同步,手續費是交易所的真實數字"}

    exec_id = Column(String(64), primary_key=True, comment="Bybit execId")
    run_id = Column(String(36), nullable=False, index=True, comment="屬於哪次啟動(對應 sl_run.run_id)")
    order_id = Column(String(64), comment="對應的 Bybit orderId(sl_order.order_id);資金費為 NULL 或空")
    event_index = Column(Integer, comment="屬於第幾輪(對應 sl_event.event_index)")
    exec_type = Column(String(20), nullable=False, comment="Trade 成交 / Funding 資金費結算")
    side = Column(String(4), comment="Buy / Sell")
    price = Column(MONEY, comment="成交價")
    qty = Column(MONEY, comment="成交數量")
    fee = Column(MONEY, comment="手續費或資金費(USDT):正數 = 付出,負數 = 收到")
    is_maker = Column(Boolean, comment="true = 掛單成交(maker,手續費較低);false = 吃單(taker)")
    exec_time = Column(TS, nullable=False, comment="成交時間")


class SlEvent(Base):
    __tablename__ = "sl_event"
    __table_args__ = {"comment": "每一輪(部位 0 → 有倉 → 0)一列:均價、毛利、手續費、資金費、淨利、最大回撤"}

    run_id = Column(String(36), primary_key=True, comment="屬於哪次啟動(對應 sl_run.run_id)")
    event_index = Column(Integer, primary_key=True, comment="第幾輪(1 起算)")
    direction = Column(String(10), nullable=False, comment="long 做多 / short 做空")
    start_time = Column(TS, nullable=False, comment="這輪第一筆成交時間(開始有倉)")
    end_time = Column(TS, nullable=False, comment="這輪部位回到 0 的時間")
    fills = Column(Integer, nullable=False, comment="這輪成交幾次")
    max_position = Column(MONEY, nullable=False, comment="這輪最大持倉數量")
    avg_entry = Column(MONEY, comment="建倉均價")
    avg_exit = Column(MONEY, comment="平倉均價")
    realized_pnl = Column(MONEY, nullable=False, comment="價差毛利(USDT,未扣手續費、資金費)")
    fees = Column(MONEY, nullable=False, comment="手續費合計(USDT,正數 = 付出)")
    funding = Column(MONEY, nullable=False, comment="資金費合計(USDT,正數 = 付出)")
    net_pnl = Column(MONEY, nullable=False, comment="淨利(USDT)= realized_pnl − fees − funding")
    max_drawdown = Column(MONEY, nullable=False, comment="這輪持倉期間最大浮虧(USDT,<= 0)")
    forced = Column(Boolean, nullable=False, comment="true = 這輪是收尾時市價強制平倉結束的")


MODELS = {m.__tablename__: m for m in (SlRun, SlOrder, SlFill, SlEvent)}
