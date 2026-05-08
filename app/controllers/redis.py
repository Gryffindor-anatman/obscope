from fastapi import APIRouter, HTTPException

from metrics import request_counter
from schemas.redis import SetRequest
from services import redis_service

router = APIRouter(prefix="/redis")


@router.get("/ping")
async def redis_ping():
    request_counter.add(1, {"endpoint": "/redis/ping"})
    try:
        ok = redis_service.ping()
        return {"ok": ok}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")


@router.get("/get")
async def redis_get(key: str):
    request_counter.add(1, {"endpoint": "/redis/get"})
    try:
        val = redis_service.get(key)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")
    if val is None:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return {"key": key, "value": val, "hit": True}


@router.post("/set")
async def redis_set(payload: SetRequest):
    request_counter.add(1, {"endpoint": "/redis/set"})
    try:
        ok = redis_service.set(payload.key, payload.value)
        return {"ok": ok, "key": payload.key}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")


@router.get("/keys")
async def redis_keys(pattern: str = "*"):
    request_counter.add(1, {"endpoint": "/redis/keys"})
    try:
        result = redis_service.keys(pattern)
        return {"pattern": pattern, "keys": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")
