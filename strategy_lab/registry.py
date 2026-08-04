"""
Plugin 註冊表:用一個單純的 dict 實作,不用 `importlib.metadata` 的
entry_points 機制。

Entry points 那套打包儀式,是給第三方要發布「可安裝的套件」、又要讓
host 不用改任何檔案就能自動發現時才值得用的。這裡只有一個開發者、一個
repo,新增一個 plugin 就是「一個檔案 + 在對應的
plugins/<kind>/__init__.py 加一行 import」而已。
"""

from __future__ import annotations

from typing import Dict, List, Type, TypeVar

T = TypeVar("T")

_registry: Dict[str, Dict[str, type]] = {}


class DuplicateRegistration(Exception):
    pass


class UnknownPlugin(Exception):
    pass


def register(kind: str, name: str):
    def decorator(cls: Type[T]) -> Type[T]:
        bucket = _registry.setdefault(kind, {})
        if name in bucket:
            raise DuplicateRegistration(f"{kind}:{name} is already registered")
        bucket[name] = cls
        return cls

    return decorator


def get(kind: str, name: str) -> type:
    try:
        return _registry[kind][name]
    except KeyError as exc:
        raise UnknownPlugin(f"no {kind} plugin registered as {name!r}") from exc


def list_plugins(kind: str) -> List[str]:
    return sorted(_registry.get(kind, {}).keys())
