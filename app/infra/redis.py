import redis as _redis

from config import settings

_pool: "_redis.Redis | None" = None


def get_redis() -> "_redis.Redis":
    global _pool
    if _pool is None:
        _pool = _redis.Redis.from_url(
            settings.REDIS_URL, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2,
        )
    return _pool
