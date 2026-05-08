from fastapi import APIRouter, HTTPException

from infra.db import pool_status
from metrics import request_counter
from schemas.user import CreateUserRequest
from services import user_service

router = APIRouter()


@router.get("/mysql/ping")
async def mysql_ping():
    request_counter.add(1, {"endpoint": "/mysql/ping"})
    try:
        ok = await user_service.ping()
        return {"ok": ok}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


@router.get("/mysql/users")
async def mysql_users():
    request_counter.add(1, {"endpoint": "/mysql/users"})
    try:
        return await user_service.list_users()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


@router.get("/mysql/slow")
async def mysql_slow(seconds: int = 3):
    request_counter.add(1, {"endpoint": "/mysql/slow"})
    try:
        ok = await user_service.slow_query(seconds)
        return {"ok": ok, "seconds": seconds}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


@router.post("/mysql/users")
async def mysql_create_user(payload: CreateUserRequest):
    request_counter.add(1, {"endpoint": "/mysql/users"})
    try:
        return await user_service.create_user(payload.name, payload.email)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


@router.get("/debug/pool")
async def debug_pool():
    return pool_status()
