"""Streamlit 第二階段的動作層:實盤啟動 / 運作中改參數 / 停止 / 脫離 / 改設定檔(2026-10-09)。

頁面只是外殼,背後全部走跟終端機同一套程式與檢查:
- 預覽 + 啟動 → live/preflight.py 的 main()(預覽時回答空字串 = 取消;啟動時把使用者在頁面上
  打的字原樣交給它,實盤一樣要 `yes`)
- 改建倉價 / 平倉距離 / loop → live/control.py 的 main()(在跑:送請求給背景程式;沒在跑:寫回設定檔)
- 停止(收尾:取消掛單、市價平倉)/ 脫離(不收尾)→ live/daemon.py

額外的保護(終端機版沒有、頁面才需要的):
- 啟動前一定要先預覽,而且預覽要在 10 分鐘內、設定檔在預覽之後沒被改過
- 停止要打 STOP、脫離要打 DETACH
- 背景程式在跑的時候不能改基本設定(只能用 control 改那三個參數)
這裡不 import streamlit,可以單獨測試。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from strategy_lab.live import control, daemon, preflight

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = PROJECT_ROOT / "live_execution_config.example.yaml"
PREVIEW_MAX_AGE_SECONDS = 10 * 60

# 頁面上可以改的基本設定(其他欄位請直接編輯檔案);position_sizing.* 是巢狀欄位
EDITABLE = {"strategy_path", "symbol_override", "dry_run", "testnet", "adopt_existing_position",
            "position_sizing.mode", "position_sizing.value"}


# ---------------------------------------------------------------------------
# 設定檔
# ---------------------------------------------------------------------------

def daemon_running(config_path: Path) -> bool:
    return control.is_running(Path(config_path))


def new_config(project_root: Path, name: str, example: Path = EXAMPLE_CONFIG) -> Path:
    """從範本建立 live_<name>.yaml(範本預設 dry_run / testnet 都是 true)。"""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name or ""):
        raise ValueError("名稱只能用英文、數字、底線、減號")
    path = Path(project_root) / f"live_{name}.yaml"
    if path.exists():
        raise ValueError(f"{path.name} 已經存在")
    shutil.copyfile(example, path)
    return path


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    text = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True).strip()
    return text[:-4].strip() if text.endswith("\n...") or text.endswith("...") else text


def _replace_line(text: str, pattern: str, value: str) -> Tuple[str, bool]:
    """把符合 pattern 那一行的值換掉,行尾註解保留。"""
    regex = re.compile(pattern + r"(?P<value>[^#\n]*?)(?P<comment>[ \t]+#[^\n]*)?$", re.MULTILINE)
    m = regex.search(text)
    if not m:
        return text, False
    new = m.group("head") + value + (m.group("comment") or "")
    return text[: m.start()] + new + text[m.end():], True


def set_config_values(config_path: Path, values: Dict[str, Any]) -> None:
    """改設定檔的基本欄位,保留註解。寫入前確認改出來的 YAML 每個值都對,不對就不寫。"""
    config_path = Path(config_path)
    unknown = sorted(set(values) - EDITABLE)
    if unknown:
        raise ValueError(f"頁面上不能改 {unknown};可以改:{sorted(EDITABLE)}")
    if daemon_running(config_path):
        raise ValueError(f"{config_path.stem} 正在跑,不能改基本設定(建倉價 / 平倉距離 / loop 請用「運作中改參數」)")
    text = config_path.read_text(encoding="utf-8")
    for key, value in values.items():
        scalar = _yaml_scalar(value)
        if key.startswith("position_sizing."):
            sub = key.split(".", 1)[1]
            text, ok = _replace_line(text, rf"(?P<head>^position_sizing:[^\n]*\n(?:[ \t]+[^\n]*\n)*?[ \t]+{sub}:[ \t]*)", scalar)
            if not ok:
                raise ValueError(f"設定檔裡找不到 position_sizing.{sub},請手動修改")
        else:
            text, ok = _replace_line(text, rf"(?P<head>^{key}:[ \t]*)", scalar)
            if not ok:
                text = text.rstrip("\n") + f"\n{key}: {scalar}\n"
    check = yaml.safe_load(text) or {}
    for key, value in values.items():
        got = check.get("position_sizing", {}).get(key.split(".", 1)[1]) if key.startswith("position_sizing.") \
            else check.get(key)
        if got != value and not (isinstance(value, float) and isinstance(got, (int, float)) and float(got) == value):
            raise ValueError(f"無法安全地改寫 {config_path.name} 的 {key},沒有寫入,請手動修改")
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(config_path)


def _symbol_of(path: Path) -> Optional[str]:
    try:
        return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("symbol_override")
    except Exception:  # noqa: BLE001
        return None


def config_for(project_root: Path, strategy_path: str, symbol: str) -> Path:
    """選策略 + 幣種 → 用哪一份 live_*.yaml:已經有同策略同幣種的就沿用,沒有就自動命名
    live_<策略>_<幣種>.yaml(還不建立)。使用者不用管設定檔(2026-10-09)。"""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("請填幣種")
    for path in sorted(Path(project_root).glob("live_*.yaml")):
        if path.name.endswith(".example.yaml"):
            continue
        try:
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        if cfg.get("strategy_path") == strategy_path and str(cfg.get("symbol_override") or "").upper() == symbol:
            return path
    return Path(project_root) / f"live_{Path(strategy_path).stem}_{symbol.lower()}.yaml"


def _strategy_file(project_root: Path, strategy_path: str) -> Path:
    local = Path(project_root) / strategy_path
    return local if local.exists() else PROJECT_ROOT / strategy_path


def prepare_config(project_root: Path, strategy_path: str, symbol: str, settings: Dict[str, Any],
                   params: Dict[str, Any], example: Path = EXAMPLE_CONFIG) -> Path:
    """把頁面上填的策略、幣種、基本設定(settings,見 EDITABLE)與策略參數(params:entry_prices /
    distance / loop)寫進對應的設定檔;沒有就從範本建立。先全部驗證,不合法什麼都不寫。
    頁面只做真正的交易環境:一律寫 dry_run: false、testnet: false,settings 不能帶這兩個
    (2026-10-09 使用者要求;dry-run / 測試網仍可從終端機用)。"""
    live_only = sorted({"dry_run", "testnet"} & set(settings))
    if live_only:
        raise ValueError(f"頁面只做真正的交易環境,不能設定 {live_only}(dry-run / 測試網請用終端機)")
    path = config_for(project_root, strategy_path, symbol)
    if path.exists() and daemon_running(path):
        raise ValueError(f"{path.stem} 正在跑,不能改設定(要改參數請用進行中清單的「改參數」)")
    info = strategy_info(_strategy_file(project_root, strategy_path))
    changes: Dict[str, Any] = {}
    if info["scale_in"] and params.get("entry_prices") is not None:
        from strategy_lab.engine.scale_in_runner import validate_entry_prices

        changes["entry_prices"] = validate_entry_prices(params["entry_prices"], info["lots"])
    if info["scale_in"] and params.get("distance") is not None:
        unit = (info.get("distance") or {}).get("unit", "pct")
        changes["distance"] = {"value": float(params["distance"]), "unit": unit}
    if info.get("band") and params.get("band") is not None:  # 區間策略的買賣 %(2026-10-10)
        from strategy_lab.plugins.entry.band import BandEntry

        band = BandEntry(buy_pct=params["band"].get("buy_pct"), sell_pct=params["band"].get("sell_pct"))  # 驗證
        changes["band"] = {"buy_pct": band.buy_pct, "sell_pct": band.sell_pct}
    if "loop" in params and not info.get("band"):  # 區間策略固定做到收尾,不寫 loop
        loop = params["loop"]
        if loop is not None and (isinstance(loop, bool) or not isinstance(loop, int) or loop < 0):
            raise ValueError(f"loop 要是 0 以上的整數或 null,收到 {loop!r}")
        changes["loop"] = loop

    created = not path.exists()
    if created:
        shutil.copyfile(example, path)
    try:
        set_config_values(path, {**settings, "strategy_path": strategy_path,
                                 "symbol_override": symbol.strip().upper(),
                                 "dry_run": False, "testnet": False})  # 頁面只做真正的交易環境(2026-10-09)
        if changes:
            control.write_back(path, changes)
    except Exception:
        if created:
            path.unlink(missing_ok=True)  # 新建到一半失敗,不留半成品
        raise
    return path


def fingerprint(config_path: Path) -> str:
    return hashlib.sha256(Path(config_path).read_bytes()).hexdigest()


def list_strategies(project_root: Path = PROJECT_ROOT) -> List[Path]:
    return sorted((Path(project_root) / "strategies").glob("*.yaml"))


def strategy_info(path: Path) -> Dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    weights = ((raw.get("entry") or {}).get("params") or {}).get("weights") or []
    entry_params = (raw.get("entry") or {}).get("params") or {}
    return {"name": raw.get("name"), "direction": raw.get("direction", "long"), "scale_in": bool(raw.get("scale_in")),
            "band": bool(raw.get("band")), "buy_pct": entry_params.get("buy_pct"), "sell_pct": entry_params.get("sell_pct"),
            "lots": len(weights) if raw.get("scale_in") else 0, "loop": raw.get("loop"),
            "expected_hold": raw.get("expected_hold"),
            "distance": ((raw.get("exit") or {}).get("params") or {}).get("distance")}


# ---------------------------------------------------------------------------
# 預覽 + 啟動(preflight)
# ---------------------------------------------------------------------------

@dataclass
class PreviewResult:
    text: str
    ready: bool  # 檢查都通過、走到「輸入 yes」那一步
    fingerprint: str  # 預覽當下設定檔的內容
    at: float  # time.time()


def _run(main, argv: List[str], answer: str, **extra) -> Tuple[int, str, bool]:
    lines: List[str] = []
    asked = {"v": False}

    def input_fn(prompt: str) -> str:
        asked["v"] = True
        lines.append(prompt.strip())
        return answer

    def print_fn(*args: Any, **kwargs: Any) -> None:
        lines.append(" ".join(str(a) for a in args))

    try:
        rc = main(argv=argv, input_fn=input_fn, print_fn=print_fn, **extra)
    except Exception as e:  # noqa: BLE001  設定錯誤之類的,顯示在頁面上
        lines.append(f"✗ {type(e).__name__}: {e}")
        rc = 1
    return rc, "\n".join(lines), asked["v"]


def preflight_preview(config_path: Path) -> PreviewResult:
    """跑一次 preflight,到「輸入 yes」那一步回答空字串 = 取消。只查資料,不啟動。"""
    fp = fingerprint(config_path)
    rc, text, asked = _run(preflight.main, ["--config", str(config_path)], "", start_fn=daemon.start)
    return PreviewResult(text=text, ready=(rc == 0 and asked), fingerprint=fp, at=time.time())


def preflight_start(config_path: Path, preview: Optional[PreviewResult], typed: str) -> Tuple[bool, str]:
    """使用者在頁面上確認後才呼叫。preflight 會重新查一次市場並重印計畫,再把 typed 當成使用者的輸入
    (實盤要 yes);檢查不過或輸入不對都不會啟動。"""
    if preview is None or not preview.ready:
        raise ValueError("要先按「預覽」,而且預覽的檢查都要通過")
    if time.time() - preview.at > PREVIEW_MAX_AGE_SECONDS:
        raise ValueError("預覽已經超過 10 分鐘,市場可能變了,請重新預覽")
    if fingerprint(config_path) != preview.fingerprint:
        raise ValueError("設定檔在預覽之後改過了,請重新預覽")
    rc, text, _ = _run(preflight.main, ["--config", str(config_path)], typed, start_fn=daemon.start)
    return (rc == 0 and "已在背景啟動" in text), text


# ---------------------------------------------------------------------------
# 運作中改參數(control)
# ---------------------------------------------------------------------------

def _assignments(changes: Dict[str, Any]) -> List[str]:
    items = []
    if "entry_prices" in changes:
        items.append("entry_prices=" + ",".join(f"{float(p):g}" for p in changes["entry_prices"]))
    if "distance" in changes:
        d = changes["distance"]
        items.append(f"distance={d:g}" if isinstance(d, (int, float)) else f"distance={d}")
    if "loop" in changes:
        items.append("loop=" + ("null" if changes["loop"] is None else str(int(changes["loop"]))))
    if not items:
        raise ValueError("沒有要改的參數")
    return items


def control_preview(config_path: Path, changes: Dict[str, Any]) -> str:
    _, text, _ = _run(control.main, ["--config", str(config_path), "set", *_assignments(changes)], "")
    return text


def control_apply(config_path: Path, changes: Dict[str, Any], typed: str) -> Tuple[bool, str]:
    """背景程式在跑:送請求、等它套用(最多 90 秒);沒在跑:寫回設定檔。typed 交給 control(實盤要 yes)。"""
    rc, text, _ = _run(control.main, ["--config", str(config_path), "set", *_assignments(changes)], typed)
    return rc == 0 and "✓" in text, text


# ---------------------------------------------------------------------------
# 停止 / 脫離
# ---------------------------------------------------------------------------

def stop(config_path: Path, typed: str) -> str:
    """收尾:取消所有掛單、市價平倉後結束(最多等 90 秒)。"""
    if typed.strip() != "STOP":
        raise ValueError("要在框裡打 STOP(大寫)才會停止")
    return daemon.stop(Path(config_path))


def detach(config_path: Path, typed: str) -> str:
    """脫離:直接結束、不收尾,掛單與持倉留在交易所給下次接手。"""
    if typed.strip() != "DETACH":
        raise ValueError("要在框裡打 DETACH(大寫)才會脫離")
    return daemon.detach(Path(config_path))
