import time

from fastapi import APIRouter

from metrics import request_counter, request_duration
from services import composite_service

router = APIRouter()


@router.get("/all")
async def all_services():
    start = time.perf_counter()
    request_counter.add(1, {"endpoint": "/all"})
    result = await composite_service.call_all()
    duration_ms = (time.perf_counter() - start) * 1000
    request_duration.record(duration_ms, {"endpoint": "/all"})
    return result
