from fastapi import APIRouter, HTTPException, Request

from auth_token import check_token
from metrics import request_counter
from schemas.redis import SetRequest
from services import redis_service

router = APIRouter(prefix="/redis")


@router.get("/ping")
@check_token
async def redis_ping(request: Request):
    request_counter.add(1, {"endpoint": "/redis/ping"})
    try:
        ok = redis_service.ping()
        return {"ok": ok}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")


@router.get("/get")
@check_token
async def redis_get(request: Request, key: str):
    request_counter.add(1, {"endpoint": "/redis/get"})
    try:
        val = redis_service.get(key)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")
    if val is None:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return {"key": key, "value": val, "hit": True}


@router.post("/set")
@check_token
async def redis_set(request: Request, payload: SetRequest):
    request_counter.add(1, {"endpoint": "/redis/set"})
    try:
        ok = redis_service.set(payload.key, payload.value)
        return {"ok": ok, "key": payload.key}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")


@router.get("/keys")
@check_token
async def redis_keys(request: Request, pattern: str = "*"):
    request_counter.add(1, {"endpoint": "/redis/keys"})
    try:
        result = redis_service.keys(pattern)
        return {"pattern": pattern, "keys": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")
