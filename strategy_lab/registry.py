"""
Plugin registry: a plain dict, not `importlib.metadata` entry_points.

Entry points earn their packaging ceremony when third parties ship
installable packages that need to be discovered without the host editing
any file. Here there is one developer, one repo, and a new plugin is one
file plus one import line in the matching plugins/<kind>/__init__.py.
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
