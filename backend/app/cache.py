"""
Lightweight in-memory TTL cache for read-heavy endpoints.

Simple dict-based cache with automatic expiration. No Redis needed.
Invalidate by prefix after refresh/build operations.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TTL = 180  # 3 minutes


class SimpleCache:
    """TTL cache for a single-process FastAPI server."""

    def __init__(self, default_ttl: int = DEFAULT_TTL):
        self._store: dict[str, tuple[float, Any]] = {}
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            self._misses += 1
            return None
        self._hits += 1
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expires_at = time.monotonic() + (ttl or self._default_ttl)
        self._store[key] = (expires_at, value)

    def invalidate(self, prefix: str = "") -> int:
        """Remove all keys matching prefix. Empty prefix clears everything."""
        if not prefix:
            count = len(self._store)
            self._store.clear()
            if count:
                logger.info("Cache cleared: %d entries", count)
            return count
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            del self._store[k]
        if keys:
            logger.info("Cache invalidated %d entries matching '%s'", len(keys), prefix)
        return len(keys)

    def stats(self) -> dict:
        return {
            "entries": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
        }


# Module-level singleton
cache = SimpleCache()
