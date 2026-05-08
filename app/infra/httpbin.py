import httpx

from config import settings


async def get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{settings.HTTPBIN_URL}{path}")
        return resp.json()


async def post(path: str, payload: dict | None) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(f"{settings.HTTPBIN_URL}{path}", json=payload or {})
        return resp.json()
