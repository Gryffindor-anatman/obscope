import logging

from infra import httpbin
from metrics import httpbin_requests_total

logger = logging.getLogger(__name__)


async def proxy_get() -> dict:
    httpbin_requests_total.add(1, {"endpoint": "/get"})
    data = await httpbin.get("/get")
    logger.info("proxied httpbin GET ok")
    return data


async def proxy_post(payload: "dict | None") -> dict:
    httpbin_requests_total.add(1, {"endpoint": "/post"})
    data = await httpbin.post("/post", payload)
    logger.info("proxied httpbin POST ok")
    return data


async def get_uuid() -> str:
    httpbin_requests_total.add(1, {"endpoint": "/uuid"})
    data = await httpbin.get("/uuid")
    return data.get("uuid")
