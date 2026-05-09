from functools import wraps

from fastapi import HTTPException, Request

from services.auth_service import verify_basic_auth


def check_token(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        request = kwargs.get("request") or next(
            (a for a in args if isinstance(a, Request)), None
        )
        if request is None:
            raise HTTPException(500, "endpoint must declare request: Request")

        auth = request.headers.get("Authorization", "")
        credential = await verify_basic_auth(auth)
        if credential is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        return await func(*args, **kwargs)

    return wrapper
