"""Rate limiting. In-memory backend for tests/local; Redis backend (shared across workers) for
staging/production. Fixed-window counters keyed by caller-chosen strings."""

from __future__ import annotations

import threading
import time
from typing import Protocol

from artify_api.errors import RateLimited
from artify_api.settings import get_settings


class Limiter(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Record a hit. Return (allowed, retry_after_seconds)."""

    def reset(self) -> None: ...


class MemoryLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}  # key -> (window_start, count)

    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = int(time.time())
        with self._lock:
            start, count = self._windows.get(key, (now, 0))
            if now - start >= window_seconds:
                start, count = now, 0
            count += 1
            self._windows[key] = (start, count)
            return count <= limit, max(1, window_seconds - (now - start))

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


class RedisLimiter:
    def __init__(self, url: str) -> None:
        import redis  # local import: only needed when configured

        self._r = redis.Redis.from_url(url)

    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        k = f"rl:{key}"
        pipe = self._r.pipeline()
        pipe.incr(k)
        pipe.expire(k, window_seconds, nx=True)
        pipe.ttl(k)
        count, _, ttl = pipe.execute()
        return int(count) <= limit, max(1, int(ttl))

    def reset(self) -> None:
        for k in self._r.scan_iter("rl:*"):
            self._r.delete(k)


_limiter: Limiter | None = None


def get_limiter() -> Limiter:
    global _limiter
    if _limiter is None:
        s = get_settings()
        _limiter = RedisLimiter(s.redis_url) if s.rate_limit_backend == "redis" else MemoryLimiter()
    return _limiter


def set_limiter(limiter: Limiter | None) -> None:
    global _limiter
    _limiter = limiter


def enforce(key: str, limit: int, window_seconds: int) -> None:
    allowed, retry_after = get_limiter().hit(key, limit, window_seconds)
    if not allowed:
        raise RateLimited(
            "Too many attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
            extra={"retry_after": retry_after},
        )
