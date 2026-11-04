"""In-process request counters for the platform health page. Per process, reset on restart; this is
NOT a monitoring system and the health page labels it as such."""

from __future__ import annotations

import threading
import time
from collections import Counter

_lock = threading.Lock()
_counts: Counter[str] = Counter()
STARTED_AT = time.time()


def record_response(status: int) -> None:
    with _lock:
        _counts[f"{status // 100}xx"] += 1


def snapshot() -> dict[str, int]:
    with _lock:
        return {k: _counts.get(k, 0) for k in ("2xx", "3xx", "4xx", "5xx")}
