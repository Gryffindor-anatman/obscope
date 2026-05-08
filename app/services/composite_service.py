import logging
import time

from opentelemetry import trace

from services import httpbin_service, redis_service, user_service

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


async def call_all() -> dict:
    result: dict = {}
    with tracer.start_as_current_span("composite_call") as span:
        try:
            ts = str(time.time())
            redis_service.set("demo:composite_hit", ts)
            got = redis_service.get("demo:composite_hit")
            result["redis"] = {"ok": True, "value": got}
        except Exception as e:
            result["redis"] = {"ok": False, "error": str(e)}

        try:
            rows = await user_service.list_users()
            result["mysql"] = {"ok": True, "user_count": len(rows)}
        except Exception as e:
            result["mysql"] = {"ok": False, "error": str(e)}

        try:
            uuid = await httpbin_service.get_uuid()
            result["httpbin"] = {"ok": True, "uuid": uuid}
        except Exception as e:
            result["httpbin"] = {"ok": False, "error": str(e)}

        span.set_attribute(
            "composite.all_ok", all(v.get("ok") for v in result.values())
        )

    logger.info(
        "composite call redis=%s mysql=%s httpbin=%s",
        result["redis"]["ok"], result["mysql"]["ok"], result["httpbin"]["ok"],
    )
    return result
