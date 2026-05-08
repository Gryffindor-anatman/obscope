import asyncio
import logging
import time

from infra.redis import get_redis
from metrics import redis_ops_total

logger = logging.getLogger(__name__)


async def redis_heartbeat() -> None:
    while True:
        try:
            ts = str(time.time())
            r = get_redis()
            r.set("demo:last_check", ts)
            got = r.get("demo:last_check")
            redis_ops_total.add(1, {"operation": "set"})
            redis_ops_total.add(1, {"operation": "get"})
            logger.info("background redis heartbeat set_ts=%s readback=%s", ts, got)
        except Exception as e:
            logger.warning("background redis heartbeat failed: %s", e)
        await asyncio.sleep(30)
