import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import obs

from config import settings

logger = logging.getLogger("app")

_bg_task: "asyncio.Task | None" = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _bg_task
    from background import redis_heartbeat
    from infra.db import init_schema

    logger.info("app starting service=%s", settings.SERVICE_NAME)
    init_schema()
    _bg_task = asyncio.create_task(redis_heartbeat())
    yield
    if _bg_task:
        _bg_task.cancel()
    logger.info("app stopping")


app = FastAPI(lifespan=lifespan)
obs.init(app, service_name=settings.SERVICE_NAME)

# Routers are imported AFTER obs.init so any module-level meter/tracer
# acquisition runs against the configured OTel SDK.
from controllers import composite, health, httpbin, mysql, redis  # noqa: E402

app.include_router(health.router)
app.include_router(redis.router)
app.include_router(mysql.router)
app.include_router(httpbin.router)
app.include_router(composite.router)
