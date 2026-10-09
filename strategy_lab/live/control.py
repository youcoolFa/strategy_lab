"""
strategy_lab/live/control.py

策略**運作中**改參數(2026-10-07):建倉價(entry_prices)、平倉距離(distance)、loop。

    .venv/bin/python -m strategy_lab.live.control --config live_execution_config.yaml \\
        set entry_prices=1.2300,1.2294,1.2288 distance=0.3 loop=3

流程(比照 preflight:先預覽、輸入 yes 才生效):
    1. 預覽:舊值 → 新值、每注新的建倉/平倉價與離現價多遠、越過現價的警告
    2. 輸入 yes(dry-run 設定檔輸入 y)
    3. 背景程式在跑 → 寫請求檔 run/<設定檔>.control.json;背景程式在兩個 tick 之間讀到
       (process_control),用當下價格再驗證一次、套用(runner.apply_changes)、寫回設定檔、
       回寫結果檔 run/<設定檔>.control.result.json;這裡等結果並顯示「已套用 / 被拒絕 + 原因」
       沒在跑 → 直接寫回設定檔,下次啟動生效
    4. log、Telegram 都有紀錄(🔧 參數已更新 / 被拒絕)

寫回設定檔:
    - entry_prices → 改 live_execution_config.yaml 的 entry_prices 那一行(保留行尾註解)
    - distance、loop → 寫進 strategy_overrides 區塊(蓋過策略 YAML,不改共用的 strategies/*.yaml);
      live/main.py 的 apply_strategy_overrides() 在啟動和 preflight 時套用
    其他行(含註解)原樣保留;寫入前確認改出來的還是合法 YAML,不對就不寫。

只能改這三個;幣種、方向、策略類型、數量要停止後重新啟動。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from dotenv import load_dotenv
from loguru import logger

from strategy_lab.live import daemon

RUN_DIR = daemon.RUN_DIR
DISTANCE_UNITS = ("pct", "points")
WAIT_SECONDS = 90  # 背景程式每 5 秒一個 tick;重掛單遇到網路重試也要時間
MAX_REQUEST_AGE_SECONDS = 600  # 超過 10 分鐘的請求不套用(例如當時在跑的是不認得請求的舊版程式)


# ---------------------------------------------------------------- 解析指令

def parse_assignments(items: List[str], current_distance: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把 ["entry_prices=1.2,1.1", "distance=0.3", "loop=3"] 轉成 changes dict。不合法就 ValueError。"""
    changes: Dict[str, Any] = {}
    for item in items:
        key, sep, raw = item.partition("=")
        key, raw = key.strip(), raw.strip()
        if not sep:
            raise ValueError(f"看不懂 {item!r},格式是 key=value(entry_prices / distance / loop)")
        if key == "entry_prices":
            try:
                prices = [float(x) for x in raw.split(",") if x.strip()]
            except ValueError:
                raise ValueError(f"entry_prices 要是逗號分隔的數字,收到 {raw!r}") from None
            if not prices:
                raise ValueError("entry_prices 不能是空的")
            changes["entry_prices"] = prices
        elif key == "distance":
            m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*(pct|points)?", raw)
            if not m:
                raise ValueError(f"distance 要是數字,可加單位 pct / points(例 0.3、500points),收到 {raw!r}")
            unit = m.group(2) or (current_distance or {}).get("unit") or "pct"
            changes["distance"] = {"value": float(m.group(1)), "unit": unit}
        elif key == "loop":
            if raw.lower() in ("null", "none", "不限"):
                changes["loop"] = None
            elif raw.isdigit():
                changes["loop"] = int(raw)
            else:
                raise ValueError(f"loop 要是 0 以上的整數或 null(不限次數),收到 {raw!r}")
        else:
            raise ValueError(f"不能在運作中改 {key!r};只能改 entry_prices、distance、loop")
    return changes


# ---------------------------------------------------------------- 寫回設定檔

_ENTRY_LINE = re.compile(r"^entry_prices:[ \t]*[^#\n]*?(?P<comment>[ \t]+#[^\n]*)?$", re.MULTILINE)
_OVERRIDES_HEADER = "strategy_overrides:   # 運作中用 strategy_lab.live.control 改的參數,蓋過策略 YAML(2026-10-07 起)"


def _remove_top_level_block(text: str, key: str) -> str:
    lines, out, skipping = text.split("\n"), [], False
    for line in lines:
        if line.startswith(f"{key}:"):
            skipping = True
            continue
        if skipping and (line.startswith((" ", "\t")) or line == ""):
            if line == "":
                skipping = False  # 空行結束區塊,保留空行
                out.append(line)
            continue
        skipping = False
        out.append(line)
    return "\n".join(out)


def write_back(config_path: Path, changes: Dict[str, Any]) -> None:
    config_path = Path(config_path)
    text = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}

    if "entry_prices" in changes:
        prices = changes["entry_prices"]
        new_line = "entry_prices: [" + ", ".join(f"{p:g}" for p in prices) + "]"
        m = _ENTRY_LINE.search(text)
        if m:
            text = text[: m.start()] + new_line + (m.group("comment") or "") + text[m.end():]
        else:
            text = text.rstrip("\n") + "\n" + new_line + "\n"

    overrides = dict(data.get("strategy_overrides") or {})
    if "loop" in changes:
        overrides["loop"] = changes["loop"]
    if "distance" in changes:
        overrides["exit_distance"] = dict(changes["distance"])
    if "band" in changes:  # 區間策略的買賣 %(2026-10-10,頁面寫的;運作中不能改)
        overrides["band"] = {"buy_pct": float(changes["band"]["buy_pct"]), "sell_pct": float(changes["band"]["sell_pct"])}
    if "loop" in changes or "distance" in changes or "band" in changes:
        text = _remove_top_level_block(text, "strategy_overrides").rstrip("\n")
        block = [_OVERRIDES_HEADER]
        if "loop" in overrides:
            block.append(f"  loop: {'null' if overrides['loop'] is None else overrides['loop']}")
        if "exit_distance" in overrides:
            d = overrides["exit_distance"]
            block.append(f"  exit_distance: {{value: {d['value']:g}, unit: {d['unit']}}}")
        if "band" in overrides:
            band = overrides["band"]
            block.append(f"  band: {{buy_pct: {band['buy_pct']:g}, sell_pct: {band['sell_pct']:g}}}")
        text += "\n\n" + "\n".join(block) + "\n"

    check = yaml.safe_load(text) or {}
    if "entry_prices" in changes and [float(p) for p in check.get("entry_prices") or []] != list(changes["entry_prices"]):
        raise ValueError(f"無法安全地改寫 {config_path} 的 entry_prices,請手動修改")
    if ("loop" in changes or "distance" in changes or "band" in changes) and check.get("strategy_overrides") != overrides:
        raise ValueError(f"無法安全地改寫 {config_path} 的 strategy_overrides,請手動修改")
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, config_path)


# ---------------------------------------------------------------- 請求 / 結果檔

def _request_path(config_path: Path, run_dir: Path) -> Path:
    return Path(run_dir) / f"{Path(config_path).stem}.control.json"


def _result_path(config_path: Path, run_dir: Path) -> Path:
    return Path(run_dir) / f"{Path(config_path).stem}.control.result.json"


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # 原子替換:背景程式不會讀到寫一半的檔


def write_request(config_path: Path, changes: Dict[str, Any], run_dir: Path = RUN_DIR) -> None:
    _result_path(config_path, run_dir).unlink(missing_ok=True)
    _write_json(_request_path(config_path, run_dir), {"created_at": time.time(), "changes": changes})


def read_request(config_path: Path, run_dir: Path = RUN_DIR) -> Optional[Dict[str, Any]]:
    """請求內容(changes);沒有請求 → None。"""
    path = _request_path(config_path, run_dir)
    return json.loads(path.read_text(encoding="utf-8"))["changes"] if path.exists() else None


def withdraw_request(config_path: Path, run_dir: Path = RUN_DIR) -> None:
    _request_path(config_path, run_dir).unlink(missing_ok=True)


def read_result(config_path: Path, run_dir: Path = RUN_DIR) -> Optional[Dict[str, Any]]:
    path = _result_path(config_path, run_dir)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def wait_result(config_path: Path, run_dir: Path = RUN_DIR, timeout: float = WAIT_SECONDS) -> Optional[Dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = read_result(config_path, run_dir)
        if result is not None:
            _result_path(config_path, run_dir).unlink(missing_ok=True)
            return result
        time.sleep(1)
    return None


# ---------------------------------------------------------------- 背景程式這一側

def process_control(runner: Any, config_path: Optional[Path], now: Any, price: float,
                    run_dir: Path = RUN_DIR) -> bool:
    """run_forever 每個 tick 之後呼叫。有請求就套用並回寫結果;回傳是否處理了一個請求。"""
    if config_path is None:
        return False
    request_path = _request_path(config_path, run_dir)
    if not request_path.exists():
        return False
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    finally:
        request_path.unlink(missing_ok=True)
    age = time.time() - float(request.get("created_at", 0))
    if age > MAX_REQUEST_AGE_SECONDS:
        logger.bind(telegram=False).warning(f"🔧 忽略 {age / 60:.0f} 分鐘前的舊參數變更請求(超過 10 分鐘不套用)")
        _write_json(_result_path(config_path, run_dir), {"ok": False, "error": f"請求是 {age / 60:.0f} 分鐘前送的,已過期不套用"})
        return True
    changes = request["changes"]
    try:
        messages = runner.apply_changes(now, price, changes)
    except ValueError as e:
        logger.warning(f"🔧 參數變更被拒絕:{e}")
        _write_json(_result_path(config_path, run_dir), {"ok": False, "error": str(e)})
        return True
    except Exception as e:  # noqa: BLE001  例如重掛單時網路錯誤:已改的參數保留,沒掛上的單下一輪補掛
        messages = [f"套用到一半遇到 {type(e).__name__}:{e};已改的參數保留,沒掛上的單下一輪會自動補掛"]
        logger.exception("🔧 參數變更套用時出錯")
    write_back(config_path, changes)
    logger.bind(telegram=True).info("🔧 參數已更新(已寫回設定檔)\n" + "\n".join(f"- {m}" for m in messages))
    _write_json(_result_path(config_path, run_dir), {"ok": True, "messages": messages})
    return True


# ---------------------------------------------------------------- 終端機指令

def is_running(config_path: Path, run_dir: Path = RUN_DIR) -> bool:
    pid = daemon._read_pid(daemon._pid_file(Path(config_path), Path(run_dir)))
    return pid is not None and daemon.is_alive(pid)


def _preview(config: Any, strategy: Any, changes: Dict[str, Any], client: Any, symbol: str,
             print_fn: Callable[..., None]) -> None:
    from strategy_lab.plugins.exit.scale_out import ScaleOutExit

    scale_in = getattr(strategy, "scale_in", False)
    print_fn("\n【變更預覽】")
    if "entry_prices" in changes:
        old = config.entry_prices or []
        print_fn("  建倉價   " + "、".join(f"{o:g} → {n:g}" for o, n in zip(old, changes["entry_prices"])))
    if "distance" in changes and isinstance(strategy.exit, ScaleOutExit):
        o, n = strategy.exit, changes["distance"]
        print_fn(f"  平倉距離 {o.value:g}{o.unit} → {n['value']:g}{n['unit']}")
    if "loop" in changes:
        def total(v):
            return "不限次數" if v is None else f"共 {v + 1} 個"
        print_fn(f"  loop     {strategy.loop} → {changes['loop']}({total(strategy.loop)} → {total(changes['loop'])})")

    if scale_in and ("entry_prices" in changes or "distance" in changes):
        price = client.get_last_price(symbol)
        prices = changes.get("entry_prices") or config.entry_prices or []
        exit_plugin = ScaleOutExit(distance=changes["distance"]) if "distance" in changes else strategy.exit
        print_fn(f"\n【改完後每注價格】現價 {price:g}")
        for i, p in enumerate(prices, 1):
            if p <= 0:
                print_fn(f"  第{i}注 不使用(建倉價 0)")
                continue
            tp = exit_plugin.exit_price_for(p, strategy.direction)
            crosses = "entry_prices" in changes and (p <= price if strategy.direction == "short" else p >= price)
            warn = "  ⚠ 越過現價:這注如果正掛著或馬上要掛,背景程式會拒絕這次變更" if crosses else ""
            print_fn(f"  第{i}注 建倉 {p:g}(離現價 {(p / price - 1) * 100:+.2f}%)→ 平倉 {tp:.6g}"
                     f"(+{abs(tp / p - 1) * 100:.2f}%){warn}")
        maker, _ = client.get_fee_rates(symbol)
        print_fn(f"  每注淨利率約 {abs(exit_plugin.exit_price_for(prices[0], strategy.direction) / prices[0] - 1) * 100 - 2 * maker * 100:.2f}%"
                 f"(平倉距離 − 兩邊掛單手續費 {2 * maker * 100:.2f}%)")
        print_fn("  已成交的注不會改建倉價;持有中的注平倉單會用新距離重掛。")


def main(argv: Optional[List[str]] = None, client_factory: Optional[Callable[[Any], Any]] = None,
         input_fn: Callable[[str], str] = input, print_fn: Callable[..., None] = print,
         run_dir: Path = RUN_DIR, is_running_fn: Callable[[Path, Path], bool] = is_running,
         wait_fn: Callable[[Path, Path, float], Optional[Dict[str, Any]]] = wait_result) -> int:
    from strategy_lab.dsl.loader import load_strategy
    from strategy_lab.live.config import load_execution_config
    from strategy_lab.live.main import apply_strategy_overrides, to_bybit_symbol
    from strategy_lab.live.preflight import _default_client

    parser = argparse.ArgumentParser(description="策略運作中改參數(建倉價、平倉距離、loop),寫回設定檔")
    parser.add_argument("--config", default=str(daemon.DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("set", help="例:set entry_prices=1.23,1.229,1.228 distance=0.3 loop=3")
    s.add_argument("assignments", nargs="+")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(f"找不到設定檔 {config_path}")
    load_dotenv()
    config = load_execution_config(config_path=config_path)
    strategy = apply_strategy_overrides(load_strategy(config.strategy_path), config)
    symbol = config.symbol_override or to_bybit_symbol(strategy.symbol)
    current_distance = getattr(strategy.exit, "distance", None)

    try:
        changes = control_changes = parse_assignments(args.assignments, current_distance)
        if ("entry_prices" in changes or "distance" in changes) and not getattr(strategy, "scale_in", False):
            raise ValueError(f"{strategy.name} 不是分注策略,只能改 loop")
        if "entry_prices" in changes:
            from strategy_lab.engine.scale_in_runner import validate_entry_prices

            changes["entry_prices"] = validate_entry_prices(changes["entry_prices"], strategy.entry.lots)
    except ValueError as e:
        print_fn(f"✗ {e}")
        return 1

    running = is_running_fn(config_path, run_dir)
    print_fn(f"策略 {strategy.name}({strategy.direction})|{symbol}|設定檔 {config_path}|"
             f"{'背景程式在跑:會立刻套用' if running else '背景程式沒有在跑:只寫設定檔,下次啟動生效'}")
    _preview(config, strategy, control_changes, (client_factory or _default_client)(config), symbol, print_fn)

    expected = ("y", "yes") if config.dry_run else ("yes",)
    prompt = "\n輸入 y 確認(dry-run): " if config.dry_run else "\n輸入 yes 確認變更(其他任何輸入取消): "
    if input_fn(prompt).strip().lower() not in expected:
        print_fn("已取消,沒有任何變更。")
        return 0

    if not running:
        write_back(config_path, changes)
        print_fn("✓ 策略沒有在跑,已寫入設定檔,下次啟動生效。")
        return 0

    write_request(config_path, changes, run_dir)
    print_fn("已送出,等背景程式套用(下一個 tick,通常幾秒內)…")
    result = wait_fn(config_path, run_dir, WAIT_SECONDS)
    if result is None:
        withdraw_request(config_path, run_dir)
        print_fn(f"✗ {WAIT_SECONDS} 秒內背景程式沒有回應,已撤回請求,沒有任何變更。"
                 "(可能是在跑的是還不支援改參數的舊版程式,重新啟動後就能用。)")
        return 1
    if not result.get("ok"):
        print_fn(f"✗ 被拒絕:{result.get('error')}(設定檔沒有變更)")
        return 1
    print_fn("✓ 已套用並寫回設定檔:")
    for m in result.get("messages", []):
        print_fn(f"  - {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
