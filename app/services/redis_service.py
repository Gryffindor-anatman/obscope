import logging

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from infra.redis import get_redis
from metrics import redis_ops_total

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def ping() -> bool:
    redis_ops_total.add(1, {"operation": "ping"})
    with tracer.start_as_current_span("redis_ping") as span:
        span.set_attribute("db.system", "redis")
        span.set_attribute("db.operation", "PING")
        try:
            ok = get_redis().ping()
            span.set_attribute("db.redis.success", ok)
            logger.info("redis PING ok=%s", ok)
            return ok
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            logger.error("redis PING failed: %s", e)
            raise


def get(key: str) -> "str | None":
    redis_ops_total.add(1, {"operation": "get"})
    with tracer.start_as_current_span("redis_get") as span:
        span.set_attribute("db.system", "redis")
        span.set_attribute("db.operation", "GET")
        span.set_attribute("db.redis.key", key)
        try:
            val = get_redis().get(key)
            span.set_attribute("db.redis.hit", val is not None)
            logger.info("redis GET key=%s hit=%s", key, val is not None)
            return val
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            logger.error("redis GET failed key=%s: %s", key, e)
            raise


def set(key: str, value: str) -> bool:
    redis_ops_total.add(1, {"operation": "set"})
    with tracer.start_as_current_span("redis_set") as span:
        span.set_attribute("db.system", "redis")
        span.set_attribute("db.operation", "SET")
        span.set_attribute("db.redis.key", key)
        try:
            ok = get_redis().set(key, value)
            span.set_attribute("db.redis.success", ok)
            logger.info("redis SET key=%s ok=%s", key, ok)
            return ok
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            logger.error("redis SET failed key=%s: %s", key, e)
            raise


def keys(pattern: str) -> list:
    redis_ops_total.add(1, {"operation": "keys"})
    with tracer.start_as_current_span("redis_keys") as span:
        span.set_attribute("db.system", "redis")
        span.set_attribute("db.operation", "KEYS")
        span.set_attribute("db.redis.pattern", pattern)
        try:
            result = get_redis().keys(pattern)
            span.set_attribute("db.redis.keys_count", len(result))
            logger.info("redis KEYS pattern=%s count=%d", pattern, len(result))
            return result
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            logger.error("redis KEYS failed pattern=%s: %s", pattern, e)
            raise
