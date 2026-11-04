"""Rate limiter backends. The Redis test needs a Redis (ARTIFY_TEST_REDIS_URL); it is skipped
when none is configured and is therefore REPORTED AS SKIPPED, not passed, in that case."""

from __future__ import annotations

import os

import pytest
from artify_api.errors import RateLimited
from artify_api.ratelimit import MemoryLimiter, RedisLimiter, enforce, set_limiter


def test_memory_limiter_blocks_after_limit() -> None:
    lim = MemoryLimiter()
    results = [lim.hit("k", 3, 60)[0] for _ in range(5)]
    assert results == [True, True, True, False, False]
    assert lim.hit("other", 3, 60)[0] is True
    set_limiter(lim)
    try:
        enforce("e", 1, 60)
        with pytest.raises(RateLimited) as exc:
            enforce("e", 1, 60)
        assert int(exc.value.headers["Retry-After"]) >= 1
    finally:
        set_limiter(None)


@pytest.mark.skipif(not os.environ.get("ARTIFY_TEST_REDIS_URL"), reason="ARTIFY_TEST_REDIS_URL not set")
def test_redis_limiter_shared_window() -> None:
    lim = RedisLimiter(os.environ["ARTIFY_TEST_REDIS_URL"])
    lim.reset()
    a = [lim.hit("shared", 2, 30)[0] for _ in range(4)]
    assert a == [True, True, False, False]
    other_instance = RedisLimiter(os.environ["ARTIFY_TEST_REDIS_URL"])
    assert other_instance.hit("shared", 2, 30)[0] is False  # state is shared across workers
    allowed, retry = other_instance.hit("fresh", 2, 30)
    assert allowed and 1 <= retry <= 30
    lim.reset()
