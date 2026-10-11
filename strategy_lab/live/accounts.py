"""多個 Bybit 帳戶(2026-10-12):依帳戶名稱從 .env 找 API key / secret / UID。

.env 寫法(照使用者的格式):
    # sub(子帳戶)
    ACCOUNT = test1                 ← 標題:給人看的;程式用它決定顯示名稱、檢查有沒有貼錯位置
    BYBIT_API_KEY=...               ← 原本那組(sat_strategy / Fa / 舊程式還在用)= test1
    BYBIT_API_SECRET=...
    BYBIT_test1_API_KEY=            ← 留空 = 用上面原本那組
    BYBIT_test1_API_SECRET=
    BYBIT_test1_UID=...             ← 選填:啟動前向 Bybit 核對 key 真的屬於這個 UID
    # main
    ACCOUNT = MAIN
    BYBIT_MAIN_API_KEY=...
    BYBIT_MAIN_API_SECRET=...
    BYBIT_MAIN_UID=...

規則:
- 帳戶名稱不分大小寫(設定檔 account: main / MAIN 都可以);顯示照 `ACCOUNT =` 的寫法。
- 沒指定帳戶 = test1(DEFAULT_ACCOUNT);test1 自己的 key 是空的 → 用原本的 BYBIT_API_KEY / SECRET。
- 其他帳戶找不到 key → AccountError,**絕不改用別的帳戶**。
- 只讀檔案,不改 os.environ;key / secret 不會出現在 repr / str / 錯誤訊息裡。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_ACCOUNT = "test1"  # 原本那組 key(BYBIT_API_KEY)的帳戶名稱

_HEADER = re.compile(r"^\s*ACCOUNT\s*=\s*(\S+)\s*$")
_KEY = re.compile(r"^\s*BYBIT_(.+)_(API_KEY|API_SECRET|UID)\s*=", re.IGNORECASE)
_FIELDS = {"API_KEY": "api_key", "API_SECRET": "api_secret", "UID": "uid"}


class AccountError(ValueError):
    """找不到帳戶或它的 key(訊息裡不會有任何 key 的值)。"""


@dataclass(frozen=True)
class AccountCredentials:
    name: str  # 顯示名稱(照 ACCOUNT = 的寫法)
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    uid: Optional[str] = None

    def __str__(self) -> str:
        return f"帳戶 {self.name}" + (f"(UID {self.uid})" if self.uid else "")


def _scan(env_path: Path) -> Tuple[List[str], Dict[str, Dict[str, str]], List[Tuple[int, str, Optional[str], str]]]:
    """(標題順序, 帳戶名稱大寫 → {欄位: 變數名稱}, [(行號, 變數名稱, 當時的標題, 名稱照原寫法)])"""
    headers: List[str] = []
    names: Dict[str, Dict[str, str]] = {}
    keys: List[Tuple[int, str, Optional[str], str]] = []
    current: Optional[str] = None
    for no, line in enumerate(Path(env_path).read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        m = _HEADER.match(line)
        if m:
            current = m.group(1)
            headers.append(current)
            continue
        m = _KEY.match(line)
        if m:
            middle, kind = m.group(1), m.group(2).upper()
            var = line.split("=", 1)[0].strip()
            names.setdefault(middle.upper(), {})[_FIELDS[kind]] = var
            keys.append((no, var, current, middle))
    return headers, names, keys


def _display(name_upper: str, headers: List[str], fallback: str) -> str:
    for h in headers:
        if h.upper() == name_upper:
            return h
    return fallback


def list_accounts(env_path: Optional[Path] = None) -> List[str]:
    """.env 裡的帳戶名稱(照檔案順序,標題優先;只有 BYBIT_<名稱>_... 沒有標題的也算)。"""
    env_path = env_path or ENV_PATH
    headers, names, keys = _scan(env_path)
    out: List[str] = []
    for h in headers:
        if h.upper() not in [o.upper() for o in out]:
            out.append(h)
    for _, _, _, middle in keys:
        if middle.upper() not in [o.upper() for o in out]:
            out.append(middle)
    return out


def resolve(name: Optional[str], env_path: Optional[Path] = None) -> AccountCredentials:
    env_path = env_path or ENV_PATH  # 呼叫時才決定(測試的 conftest 會換成假的 .env)
    wanted = (name or DEFAULT_ACCOUNT).strip()
    upper = wanted.upper()
    headers, names, _ = _scan(env_path)
    values = dotenv_values(env_path)
    found = names.get(upper, {})
    if not found and upper not in [h.upper() for h in headers] and upper != DEFAULT_ACCOUNT.upper():
        known = "、".join(list_accounts(env_path)) or "(沒有)"
        raise AccountError(f"找不到帳戶 {wanted}:.env 裡沒有 ACCOUNT = {wanted} 也沒有 BYBIT_{wanted}_API_KEY。現有帳戶:{known}")
    written = next((m for _, _, _, m in _scan(env_path)[2] if m.upper() == upper), wanted)
    display = _display(upper, headers, written)

    def get(field_name: str) -> str:
        var = found.get(field_name)
        return (values.get(var) or "").strip() if var else ""

    api_key, api_secret, uid = get("api_key"), get("api_secret"), get("uid") or None
    if not api_key and not api_secret and upper == DEFAULT_ACCOUNT.upper():  # test1 → 原本那組
        api_key = (values.get("BYBIT_API_KEY") or "").strip()
        api_secret = (values.get("BYBIT_API_SECRET") or "").strip()
    if not api_key or not api_secret:
        missing = [f"BYBIT_{display}_{k}" for k, v in (("API_KEY", api_key), ("API_SECRET", api_secret)) if not v]
        raise AccountError(f"帳戶 {display} 缺 {'、'.join(missing)}(在 .env 填上;不會改用別的帳戶)")
    return AccountCredentials(name=display, api_key=api_key, api_secret=api_secret, uid=uid)


def check_layout(env_path: Optional[Path] = None) -> List[str]:
    """提醒:寫在某個 ACCOUNT = 標題底下、名稱卻對不上的設定(可能貼錯位置)。"""
    _, _, keys = _scan(env_path or ENV_PATH)
    return [f"{var}(第 {no} 行)寫在 ACCOUNT = {header} 底下,名稱對不上,是不是貼錯位置?"
            for no, var, header, middle in keys if header is not None and middle.upper() != header.upper()]


EXPIRY_WARNING_DAYS = 7


def verify(account: AccountCredentials, info: Dict, now) -> Tuple[List[str], List[str]]:
    """拿 BybitClient.api_key_info() 的結果核對(errors, warnings)。errors 有東西 = 不能用這組 key 開始。
    - .env 有設 UID、key 卻屬於別的 UID → error(貼錯帳戶的 key)
    - 唯讀 key(不能下單)、已經過期 → error
    - 有提幣權限、7 天內到期、沒設 UID(無法核對)→ warning"""
    from datetime import datetime, timedelta

    errors: List[str] = []
    warnings: List[str] = []
    real_uid = info.get("uid")
    if account.uid and real_uid and str(real_uid) != str(account.uid):
        errors.append(f"帳戶 {account.name} 的 key 屬於 Bybit UID {real_uid},不是 .env 設定的 {account.uid}"
                      "——是不是把別的帳戶的 key 貼到這裡了?")
    if not account.uid:
        warnings.append(f"帳戶 {account.name} 沒設定 BYBIT_{account.name}_UID,無法核對;這組 key 屬於 UID {real_uid}")
    if info.get("read_only"):
        errors.append(f"帳戶 {account.name} 的 key 是唯讀的,不能下單")
    if info.get("can_withdraw"):
        warnings.append(f"帳戶 {account.name} 的 key 有提幣權限,建議到 Bybit 關掉(交易程式用不到)")
    expires = info.get("expires_at")
    if expires:
        when = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        if when <= now:
            errors.append(f"帳戶 {account.name} 的 key 已經過期({when:%Y-%m-%d})")
        elif when - now <= timedelta(days=EXPIRY_WARNING_DAYS):
            warnings.append(f"帳戶 {account.name} 的 key 將在 {when:%Y-%m-%d} 到期,記得到 Bybit 更新")
    return errors, warnings
