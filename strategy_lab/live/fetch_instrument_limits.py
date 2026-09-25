"""
live/fetch_instrument_limits.py

下載 Bybit 的商品精度限制(priceFilter/lotSizeFilter),寫進專案根目錄
的 instrument_limits.json——live/instrument_limits.py 用這份檔案修正
下單的 qty/price 精度。

這是**公開端點**,不需要真實 API key,不會碰到任何真實帳戶——查價格/
規格資料,不是下單。

只抓 strategies/*.yaml 實際會用到的 symbol,不是抓全部商品清單(Bybit
一次有幾百個交易對,抓全部沒有意義,只會讓這份檔案肥大)。

執行方式:/opt/anaconda3/bin/python3 -m strategy_lab.live.fetch_instrument_limits
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import yaml
from loguru import logger

from strategy_lab.live.bybit_client import BybitClient
from strategy_lab.live.main import to_bybit_symbol

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STRATEGIES_DIR = PROJECT_ROOT / "strategies"
OUTPUT_PATH = PROJECT_ROOT / "instrument_limits.json"


def _symbols_used_by_strategies() -> List[str]:
    symbols = set()
    for path in STRATEGIES_DIR.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text())
        symbols.add(to_bybit_symbol(raw["symbol"]))
    return sorted(symbols)


def fetch_and_save(symbols: List[str], output_path: Path = OUTPUT_PATH) -> None:
    client = BybitClient(testnet=False, api_key="", api_secret="")  # 公開端點,不需要真實 key

    existing: dict = {}
    if output_path.exists():
        existing = json.loads(output_path.read_text())

    for symbol in symbols:
        logger.info(f"下載 {symbol} 的 instrument info...")
        existing[symbol] = client.get_instrument_info(symbol)

    output_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
    logger.info(f"已寫入 {output_path}({len(existing)} 個 symbol)")


def main() -> None:
    symbols = _symbols_used_by_strategies()
    fetch_and_save(symbols)


if __name__ == "__main__":
    main()
