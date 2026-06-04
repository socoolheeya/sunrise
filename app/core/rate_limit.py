"""Collect API rate limit helper.

운영에서는 API gateway/Redis 기반 전역 rate limit 이 1차 방어선이다. 이 모듈은
collector 인스턴스 내부에서 같은 tenant 의 과도한 요청을 한 번 더 제한한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic


@dataclass
class _Window:
    started_at: float
    count: int


class TenantRateLimiter:
    def __init__(self) -> None:
        self._windows: dict[str, _Window] = {}

    def allow(self, tenant_id: str, *, limit: int, window_seconds: int = 60) -> bool:
        if limit <= 0:
            return True

        now = monotonic()
        window = self._windows.get(tenant_id)
        if window is None or now - window.started_at >= window_seconds:
            self._windows[tenant_id] = _Window(started_at=now, count=1)
            return True

        if window.count >= limit:
            return False

        window.count += 1
        return True


_collect_rate_limiter = TenantRateLimiter()


def get_collect_rate_limiter() -> TenantRateLimiter:
    return _collect_rate_limiter


def reset_state() -> None:
    global _collect_rate_limiter
    _collect_rate_limiter = TenantRateLimiter()
