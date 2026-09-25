"""
live/config.py

對應 sat_strategy/app/config.py 的 StrategyConfig/load_config() 同一種
設計:dataclass 預設值本身要是安全的,YAML 設定檔 + 環境變數才能覆蓋成
真的要上線的樣子。`dry_run`/`testnet`/`use_live_ticker_feed` 的預設值
逐項對照 sat_strategy 自己的預設值,不是隨意選的。

**這個檔案不重複放策略參數**(symbol/進出場邏輯/時間窗那些)——那些已經
在 strategies/*.yaml 裡,透過 dsl.loader.load_strategy() 讀取。這裡只放
「怎麼執行」這一層的設定(要不要真的下單、連哪個網路、輪詢間隔、要不要
用即時 feed、下多大單),跟 sat_strategy 把所有參數混在同一個
StrategyConfig 裡不一樣——Phase 3 的 DSL 已經把「策略是什麼」跟
「怎麼跑」分開了,這裡沒有必要走回頭路。

**`order_type`/`position_sizing` 這兩個也是「怎麼下單」的資料,不是策略
邏輯**——跟 dsl/order_config.py(demo/sandbox_order.yaml 用的同一套
schema)共用 `PositionSizing`,道理一樣:策略 YAML 的
threshold_price/deviation_pct 是校準給特定 symbol 用的,不該跟「這次要
下多大」混在一起。`account_percentage` 模式:不設 `account_value` 就會
自動呼叫 `BybitClient.get_account_equity()` 查真實帳戶權益;有明確設
`account_value` 才會用那個值覆蓋掉真實查詢結果,見 §6.7/§6.10。

原本用 JSON,改成 YAML 是因為:①這個 repo 其他地方(策略定義)本來就
已經在用 PyYAML,不需要為了這一份小小的執行設定額外維護兩套語法;
②YAML 可以加註解解釋每個欄位的意思,JSON 完全不行。

實際要上線的設定值(dry_run=false、testnet=false 這種),故意不由這個
程式庫自己建立/提交進 git——那是使用者自己在部署時明確建立
live_execution_config.yaml 的動作,不是「寫程式碼」這件事本身該包含
的一步。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Optional

import yaml
from loguru import logger

from strategy_lab.dsl.order_config import PositionSizing

_DEFAULT_CONFIG_YAML_PATH = Path(__file__).resolve().parents[2] / "live_execution_config.yaml"


@dataclass
class ExecutionConfig:
    # --- 要跑哪個策略(參數本身在 YAML 裡,不在這裡) ---
    strategy_path: str = "strategies/weekend_mean_reversion.yaml"
    symbol_override: Optional[str] = None  # Bybit 原生格式(如 "BTCUSDT");不設就從 YAML 的 symbol 轉換

    # --- 執行安全設定(預設值對照 sat_strategy/app/config.py) ---
    dry_run: bool = True
    testnet: bool = True
    poll_interval_seconds: int = 5
    max_api_retries: int = 5
    retry_backoff_cap_seconds: float = 30.0

    # --- 下單方式(見 dsl/order_config.py) ---
    order_type: Literal["limit", "market"] = "limit"
    position_sizing: PositionSizing = field(default_factory=lambda: PositionSizing(mode="fixed_qty", value=1.0))
    account_value: Optional[float] = None  # 只有 position_sizing.mode == account_percentage 會用到

    # --- 即時行情 feed(對照 sat_strategy 的 use_live_ticker_feed) ---
    # 預設關閉:Fa_Successful_trade 接的網路不一定跟這裡的 testnet 設定
    # 一致,用錯網路的即時價格對策略是誤導,不是幫助。
    use_live_ticker_feed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def load_execution_config(config_path: Optional[Path] = None) -> ExecutionConfig:
    """先用 ExecutionConfig 的安全預設值,若設定檔存在就覆蓋,環境變數
    最後覆蓋一次(方便在 shell 腳本裡臨時切換,不用改設定檔)。"""
    config = ExecutionConfig()
    path = config_path or _DEFAULT_CONFIG_YAML_PATH

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}  # 空檔案/全是註解時 safe_load 回傳 None
        unknown_keys = set(overrides) - set(config.to_dict())
        if unknown_keys:
            logger.warning(f"{path} 裡有不認得的欄位,已忽略: {unknown_keys}")
        for key, value in overrides.items():
            if key == "position_sizing" and isinstance(value, dict):
                value = PositionSizing(**value)
            if hasattr(config, key):
                setattr(config, key, value)
        logger.info(f"已從 {path} 載入執行參數覆蓋")

    if os.getenv("STRATEGY_LAB_DRY_RUN") is not None:
        config.dry_run = os.getenv("STRATEGY_LAB_DRY_RUN").lower() in ("1", "true", "yes")
    if os.getenv("STRATEGY_LAB_TESTNET") is not None:
        config.testnet = os.getenv("STRATEGY_LAB_TESTNET").lower() in ("1", "true", "yes")
    if os.getenv("STRATEGY_LAB_USE_LIVE_FEED") is not None:
        config.use_live_ticker_feed = os.getenv("STRATEGY_LAB_USE_LIVE_FEED").lower() in ("1", "true", "yes")

    return config
