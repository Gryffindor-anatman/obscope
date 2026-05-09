from fastapi import APIRouter, Body, HTTPException, Request

from auth_token import check_token
from metrics import request_counter
from services import httpbin_service

router = APIRouter(prefix="/proxy/httpbin")


@router.get("/get")
@check_token
async def proxy_httpbin_get(request: Request):
    request_counter.add(1, {"endpoint": "/proxy/httpbin/get"})
    try:
        return await httpbin_service.proxy_get()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"httpbin unavailable: {e}")


@router.post("/post")
@check_token
async def proxy_httpbin_post(request: Request, payload: dict = Body(None)):
    request_counter.add(1, {"endpoint": "/proxy/httpbin/post"})
    try:
        return await httpbin_service.proxy_post(payload)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"httpbin unavailable: {e}")
