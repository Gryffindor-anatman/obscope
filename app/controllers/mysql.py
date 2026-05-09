from fastapi import APIRouter, HTTPException, Request

from auth_token import check_token
from infra.db import pool_status
from metrics import request_counter
from schemas.user import CreateUserRequest
from services import user_service

router = APIRouter()


@router.get("/mysql/ping")
@check_token
async def mysql_ping(request: Request):
    request_counter.add(1, {"endpoint": "/mysql/ping"})
    try:
        ok = await user_service.ping()
        return {"ok": ok}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


@router.get("/mysql/users")
@check_token
async def mysql_users(request: Request):
    request_counter.add(1, {"endpoint": "/mysql/users"})
    try:
        return await user_service.list_users()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


@router.get("/mysql/slow")
@check_token
async def mysql_slow(request: Request, seconds: int = 3):
    request_counter.add(1, {"endpoint": "/mysql/slow"})
    try:
        ok = await user_service.slow_query(seconds)
        return {"ok": ok, "seconds": seconds}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


@router.post("/mysql/users")
@check_token
async def mysql_create_user(request: Request, payload: CreateUserRequest):
    request_counter.add(1, {"endpoint": "/mysql/users"})
    try:
        return await user_service.create_user(payload.name, payload.email)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


@router.get("/debug/pool")
async def debug_pool():
    return pool_status()
