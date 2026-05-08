import asyncio
import logging

from infra import db
from repositories import user_repo

logger = logging.getLogger(__name__)


async def list_users() -> list[dict]:
    rows = await asyncio.to_thread(user_repo.list_all)
    logger.info("mysql SELECT users count=%d", len(rows))
    return rows


async def create_user(name: str, email: str) -> dict:
    row_id = await asyncio.to_thread(user_repo.insert, name, email)
    logger.info("mysql INSERT user id=%d name=%s", row_id, name)
    return {"id": row_id, "name": name, "email": email}


async def ping() -> bool:
    ok = await asyncio.to_thread(db.ping)
    logger.info("mysql PING ok=%s", ok)
    return ok


async def slow_query(seconds: int) -> bool:
    ok = await asyncio.to_thread(db.sleep_query, seconds)
    logger.info("mysql SLEEP done seconds=%d", seconds)
    return ok
