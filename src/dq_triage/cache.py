"""Content-hashed LLM cache.

Wrap every LLM call. Key = sha256(model + messages + schema_name + extras).
Cache lives on disk (diskcache) so it survives across processes and CI runs.

Why this matters: a 250-incident x 3-seed eval is 1,500 LLM calls. Without a
cache, that is $30 and 30 minutes per run. With cache, the first run pays;
every subsequent run is free and deterministic.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from diskcache import Cache  # type: ignore[import-untyped]

T = TypeVar("T")

_DEFAULT_DIR = Path(os.getenv("DQ_LLM_CACHE_DIR", ".llm_cache"))
_cache: Cache | None = None


def _get_cache() -> Cache:
    global _cache
    if _cache is None:
        _DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
        _cache = Cache(str(_DEFAULT_DIR), size_limit=2 * 1024**3)  # 2GB cap
    return _cache


def _stable_key(payload: dict[str, object]) -> str:
    serialised = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def cached(
    *,
    model: str,
    messages: list[dict[str, object]],
    schema_name: str,
    extras: dict[str, object] | None = None,
    fn: Callable[[], T],
) -> T:
    """Return cached result if present; otherwise call `fn` and cache its return.

    `extras` is for anything that should bust the key (temperature, system prompt
    version, etc.) — keep stable for cache hits.
    """
    key = _stable_key(
        {
            "model": model,
            "messages": messages,
            "schema": schema_name,
            "extras": extras or {},
        }
    )
    cache = _get_cache()
    if key in cache:
        return cache[key]  # type: ignore[no-any-return]
    value = fn()
    cache[key] = value
    return value


def cache_stats() -> dict[str, int | float]:
    cache = _get_cache()
    return {
        "size_bytes": cache.volume(),
        "count": len(cache),
        "hit_count": getattr(cache, "hits", 0),
        "miss_count": getattr(cache, "misses", 0),
    }


def clear_cache() -> None:
    _get_cache().clear()


class BudgetExceededError(Exception):
    """Raised when the LLM budget cap is reached."""

    pass


_BUDGET_FILE = Path(os.getenv("DQ_LLM_CACHE_DIR", ".llm_cache")) / "llm_budget_usage.json"


def _get_max_budget() -> float:
    try:
        return float(os.environ.get("DQ_LLM_BUDGET_USD", "0.50"))
    except ValueError:
        return 0.50


def read_budget_spent() -> float:
    if not _BUDGET_FILE.exists():
        return 0.0
    try:
        with open(_BUDGET_FILE) as f:
            data = json.load(f)
            return float(data.get("spent", 0.0))
    except Exception:
        return 0.0


def _write_budget_spent(spent: float) -> None:
    _BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_BUDGET_FILE, "w") as f:
        json.dump({"spent": spent}, f)
