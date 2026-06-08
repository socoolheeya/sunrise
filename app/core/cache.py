"""캐시 추상화 (Port + 구현).

분석 대시보드/퍼널 같은 읽기 응답을 캐시한다.
REDIS_URL 이 설정되면 RedisCache, 아니면 NullCache(무동작) 를 사용하므로
Redis 없이도 로컬/테스트가 그대로 동작한다(graceful degradation).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Cache(ABC):
    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    async def delete(self, key: str) -> None:
        """캐시 무효화. 기본 no-op(무동작 캐시는 무시)."""
        return None

    async def delete_prefix(self, prefix: str) -> None:
        """접두사 일치 키를 일괄 무효화. 기본 no-op."""
        return None


class NullCache(Cache):
    """캐시 비활성. 항상 miss."""

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        return None


class RedisCache(Cache):
    def __init__(self, client: Any) -> None:
        self._client = client

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._client.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def delete_prefix(self, prefix: str) -> None:
        cursor = 0
        pattern = f"{prefix}*"
        while True:
            cursor, keys = await self._client.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                await self._client.delete(*keys)
            if cursor == 0:
                break


_cache: Cache | None = None


def get_cache() -> Cache:
    """설정에 따라 캐시 구현을 선택하는 싱글턴 (FastAPI Depends)."""
    global _cache
    if _cache is None:
        from app.core.config import get_settings

        settings = get_settings()
        if settings.redis_url:
            import redis.asyncio as aioredis

            client = aioredis.from_url(settings.redis_url, decode_responses=True)
            _cache = RedisCache(client)
        else:
            _cache = NullCache()
    return _cache


async def close_cache() -> None:
    """애플리케이션 종료 시 Redis 연결을 정리한다."""
    global _cache
    if _cache is None:
        return
    client = getattr(_cache, "_client", None)
    if client is not None:
        await client.aclose()
    _cache = None


def reset_state() -> None:
    """테스트 격리용."""
    global _cache
    _cache = None
