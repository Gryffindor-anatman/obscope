import logging
import time

from fastapi import APIRouter, HTTPException

from metrics import request_counter, request_duration
from services import demo_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/work")
async def work():
    start = time.perf_counter()
    request_counter.add(1, {"endpoint": "/work"})
    demo_service.do_work()
    duration_ms = (time.perf_counter() - start) * 1000
    request_duration.record(duration_ms, {"endpoint": "/work"})
    return {"ok": True, "delay_ms": round(duration_ms, 2)}


@router.get("/boom")
async def boom():
    request_counter.add(1, {"endpoint": "/boom"})
    logger.error("something went wrong on /boom")
    raise RuntimeError("boom")


@router.get("/timeout")
async def timeout_endpoint(budget_ms: int = 100):
    request_counter.add(1, {"endpoint": "/timeout"})
    within, elapsed_ms = demo_service.slow_dependency(budget_ms)
    request_duration.record(elapsed_ms, {"endpoint": "/timeout"})
    if not within:
        raise HTTPException(status_code=504, detail="upstream timeout")
    return {"ok": True, "elapsed_ms": round(elapsed_ms, 2)}
