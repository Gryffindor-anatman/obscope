import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from urllib.parse import quote_plus

import httpx
import redis
from fastapi import Body, FastAPI, HTTPException
from opentelemetry import metrics, trace
from opentelemetry.metrics import Observation
from opentelemetry.trace import Status, StatusCode
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

import obs

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "demo-api")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
MYSQL_HOST = os.getenv("MYSQL_HOST", "host.docker.internal")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DB = os.getenv("MYSQL_DB", "demoapp")
HTTPBIN_URL = os.getenv("HTTPBIN_URL", "http://host.docker.internal:80")

_redis_pool: "redis.Redis | None" = None


def get_redis() -> redis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.Redis.from_url(
            REDIS_URL, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2,
        )
    return _redis_pool


_engine: "Engine | None" = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = (
            f"mysql+pymysql://{MYSQL_USER}:{quote_plus(MYSQL_PASSWORD)}"
            f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}?charset=utf8mb4"
        )
        _engine = create_engine(
            url, pool_pre_ping=True, pool_size=5, max_overflow=5,
            connect_args={"connect_timeout": 3},
        )
        SQLAlchemyInstrumentor().instrument(engine=_engine)
        _register_pool_metrics(_engine)
        _register_statement_metrics(_engine)
    return _engine


def _register_pool_metrics(engine: Engine) -> None:
    pool = engine.pool
    meter.create_observable_gauge(
        name="db_pool_connections",
        description="SQLAlchemy connection pool state by status",
        callbacks=[lambda _: [
            Observation(pool.checkedin(), {"state": "idle"}),
            Observation(pool.checkedout(), {"state": "in_use"}),
            Observation(max(0, pool.overflow()), {"state": "overflow"}),
        ]],
    )


def _normalize_sql(s: str) -> str:
    return " ".join(s.split())[:200]


def _extract_op(s: str) -> str:
    parts = s.lstrip().split()
    return parts[0].upper() if parts else "UNKNOWN"


def _register_statement_metrics(engine: Engine) -> None:
    duration = meter.create_histogram(
        name="db_statement_duration_ms",
        description="SQL statement execution time",
        unit="ms",
    )
    errors = meter.create_counter(
        name="db_statement_errors_total",
        description="SQL statement execution failures",
    )

    @event.listens_for(engine, "before_cursor_execute")
    def _start(conn, cursor, stmt, params, context, executemany):
        context._t0 = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _end(conn, cursor, stmt, params, context, executemany):
        dt_ms = (time.perf_counter() - context._t0) * 1000
        duration.record(dt_ms, {
            "statement": _normalize_sql(stmt),
            "operation": _extract_op(stmt),
        })

    @event.listens_for(engine, "handle_error")
    def _err(exc_ctx):
        stmt = exc_ctx.statement or ""
        errors.add(1, {
            "statement": _normalize_sql(stmt),
            "operation": _extract_op(stmt),
            "exception_type": type(exc_ctx.original_exception).__name__,
        })


def init_mysql_schema() -> None:
    bootstrap_url = (
        f"mysql+pymysql://{MYSQL_USER}:{quote_plus(MYSQL_PASSWORD)}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}/?charset=utf8mb4"
    )
    bootstrap = create_engine(bootstrap_url, connect_args={"connect_timeout": 3})
    with bootstrap.begin() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {MYSQL_DB} CHARACTER SET utf8mb4"))
    bootstrap.dispose()
    with get_engine().begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(200) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))


def _query_users():
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, email, created_at FROM users")
        ).mappings().all()
        return [dict(r) for r in rows]


logger = logging.getLogger("app")


async def _background_redis_heartbeat() -> None:
    while True:
        try:
            ts = str(time.time())
            r = get_redis()
            r.set("demo:last_check", ts)
            got = r.get("demo:last_check")
            redis_ops_total.add(1, {"operation": "set"})
            redis_ops_total.add(1, {"operation": "get"})
            logger.info("background redis heartbeat set_ts=%s readback=%s", ts, got)
        except Exception as e:
            logger.warning("background redis heartbeat failed: %s", e)
        await asyncio.sleep(30)


_bg_task: "asyncio.Task | None" = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _bg_task
    logger.info("app starting service=%s", SERVICE_NAME)
    init_mysql_schema()
    _bg_task = asyncio.create_task(_background_redis_heartbeat())
    yield
    if _bg_task:
        _bg_task.cancel()
    logger.info("app stopping")


app = FastAPI(lifespan=lifespan)
obs.init(app, service_name=SERVICE_NAME)

meter = metrics.get_meter(SERVICE_NAME)
request_counter = meter.create_counter("app_requests_total")
request_duration = meter.create_histogram("app_request_duration_ms")
redis_ops_total = meter.create_counter("redis_ops_total")
httpbin_requests_total = meter.create_counter("httpbin_requests_total")

tracer = trace.get_tracer(SERVICE_NAME)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/work")
async def work():
    start = time.perf_counter()
    request_counter.add(1, {"endpoint": "/work"})
    with tracer.start_as_current_span("do_work") as span:
        delay = random.uniform(0.02, 0.15)
        span.set_attribute("simulated_delay_s", delay)
        time.sleep(delay)
        logger.info("did some work delay=%.3fs", delay)
    duration_ms = (time.perf_counter() - start) * 1000
    request_duration.record(duration_ms, {"endpoint": "/work"})
    return {"ok": True, "delay_ms": round(duration_ms, 2)}


@app.get("/boom")
async def boom():
    request_counter.add(1, {"endpoint": "/boom"})
    logger.error("something went wrong on /boom")
    raise RuntimeError("boom")


@app.get("/timeout")
async def timeout_endpoint(budget_ms: int = 100):
    start = time.perf_counter()
    request_counter.add(1, {"endpoint": "/timeout"})
    with tracer.start_as_current_span("slow_dependency") as span:
        delay = random.uniform(0.02, 0.25)
        span.set_attribute("simulated_delay_s", delay)
        span.set_attribute("budget_ms", budget_ms)
        time.sleep(delay)
    elapsed_ms = (time.perf_counter() - start) * 1000
    request_duration.record(elapsed_ms, {"endpoint": "/timeout"})
    if elapsed_ms > budget_ms:
        logger.error(
            "request timed out elapsed_ms=%.2f budget_ms=%d", elapsed_ms, budget_ms
        )
        raise HTTPException(status_code=504, detail="upstream timeout")
    return {"ok": True, "elapsed_ms": round(elapsed_ms, 2)}


# -- Redis endpoints --

@app.get("/redis/ping")
async def redis_ping():
    request_counter.add(1, {"endpoint": "/redis/ping"})
    redis_ops_total.add(1, {"operation": "ping"})
    with tracer.start_as_current_span("redis_ping") as span:
        span.set_attribute("db.system", "redis")
        span.set_attribute("db.operation", "PING")
        try:
            ok = get_redis().ping()
            span.set_attribute("db.redis.success", ok)
            logger.info("redis PING ok=%s", ok)
            return {"ok": ok}
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            logger.error("redis PING failed: %s", e)
            raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")


@app.get("/redis/get")
async def redis_get(key: str):
    request_counter.add(1, {"endpoint": "/redis/get"})
    redis_ops_total.add(1, {"operation": "get"})
    with tracer.start_as_current_span("redis_get") as span:
        span.set_attribute("db.system", "redis")
        span.set_attribute("db.operation", "GET")
        span.set_attribute("db.redis.key", key)
        try:
            val = get_redis().get(key)
            span.set_attribute("db.redis.hit", val is not None)
            logger.info("redis GET key=%s hit=%s", key, val is not None)
            if val is None:
                raise HTTPException(status_code=404, detail=f"key '{key}' not found")
            return {"key": key, "value": val, "hit": True}
        except HTTPException:
            raise
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            logger.error("redis GET failed key=%s: %s", key, e)
            raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")


@app.post("/redis/set")
async def redis_set(payload: dict = Body(...)):
    key = payload.get("key", "")
    value = payload.get("value", "")
    request_counter.add(1, {"endpoint": "/redis/set"})
    redis_ops_total.add(1, {"operation": "set"})
    with tracer.start_as_current_span("redis_set") as span:
        span.set_attribute("db.system", "redis")
        span.set_attribute("db.operation", "SET")
        span.set_attribute("db.redis.key", key)
        try:
            ok = get_redis().set(key, value)
            span.set_attribute("db.redis.success", ok)
            logger.info("redis SET key=%s ok=%s", key, ok)
            return {"ok": ok, "key": key}
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            logger.error("redis SET failed key=%s: %s", key, e)
            raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")


@app.get("/redis/keys")
async def redis_keys(pattern: str = "*"):
    request_counter.add(1, {"endpoint": "/redis/keys"})
    redis_ops_total.add(1, {"operation": "keys"})
    with tracer.start_as_current_span("redis_keys") as span:
        span.set_attribute("db.system", "redis")
        span.set_attribute("db.operation", "KEYS")
        span.set_attribute("db.redis.pattern", pattern)
        try:
            keys = get_redis().keys(pattern)
            span.set_attribute("db.redis.keys_count", len(keys))
            logger.info("redis KEYS pattern=%s count=%d", pattern, len(keys))
            return {"pattern": pattern, "keys": keys, "count": len(keys)}
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            logger.error("redis KEYS failed pattern=%s: %s", pattern, e)
            raise HTTPException(status_code=503, detail=f"redis unavailable: {e}")


# -- MySQL endpoints --

@app.get("/mysql/ping")
async def mysql_ping():
    request_counter.add(1, {"endpoint": "/mysql/ping"})
    try:
        def _ping():
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        ok = await asyncio.to_thread(_ping)
        logger.info("mysql PING ok=%s", ok)
        return {"ok": ok}
    except Exception as e:
        logger.error("mysql PING failed: %s", e)
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


@app.get("/mysql/users")
async def mysql_users():
    request_counter.add(1, {"endpoint": "/mysql/users"})
    try:
        rows = await asyncio.to_thread(_query_users)
        logger.info("mysql SELECT users count=%d", len(rows))
        return rows
    except Exception as e:
        logger.error("mysql SELECT users failed: %s", e)
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


@app.get("/debug/pool")
async def debug_pool():
    p = get_engine().pool
    return {
        "status": p.status(),
        "checkedin": p.checkedin(),
        "checkedout": p.checkedout(),
        "size": p.size(),
        "overflow": p.overflow(),
    }


@app.get("/mysql/slow")
async def mysql_slow(seconds: int = 3):
    request_counter.add(1, {"endpoint": "/mysql/slow"})
    try:
        def _sleep():
            with get_engine().connect() as conn:
                conn.execute(text("SELECT SLEEP(:s)"), {"s": seconds})
            return True
        ok = await asyncio.to_thread(_sleep)
        logger.info("mysql SLEEP done seconds=%d", seconds)
        return {"ok": ok, "seconds": seconds}
    except Exception as e:
        logger.error("mysql SLEEP failed: %s", e)
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


@app.post("/mysql/users")
async def mysql_create_user(payload: dict = Body(...)):
    name = payload.get("name", "")
    email = payload.get("email", "")
    request_counter.add(1, {"endpoint": "/mysql/users"})
    try:
        def _insert():
            with get_engine().begin() as conn:
                result = conn.execute(
                    text("INSERT INTO users (name, email) VALUES (:name, :email)"),
                    {"name": name, "email": email},
                )
                return result.lastrowid
        row_id = await asyncio.to_thread(_insert)
        logger.info("mysql INSERT user id=%d name=%s", row_id, name)
        return {"id": row_id, "name": name, "email": email}
    except Exception as e:
        logger.error("mysql INSERT failed: %s", e)
        raise HTTPException(status_code=503, detail=f"mysql unavailable: {e}")


# -- httpbin proxy endpoints --

@app.get("/proxy/httpbin/get")
async def proxy_httpbin_get():
    request_counter.add(1, {"endpoint": "/proxy/httpbin/get"})
    httpbin_requests_total.add(1, {"endpoint": "/get"})
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{HTTPBIN_URL}/get")
            logger.info("proxied httpbin GET status=%d", resp.status_code)
            return resp.json()
    except Exception as e:
        logger.error("httpbin proxy GET failed: %s", e)
        raise HTTPException(status_code=503, detail=f"httpbin unavailable: {e}")


@app.post("/proxy/httpbin/post")
async def proxy_httpbin_post(payload: dict = Body(None)):
    request_counter.add(1, {"endpoint": "/proxy/httpbin/post"})
    httpbin_requests_total.add(1, {"endpoint": "/post"})
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{HTTPBIN_URL}/post", json=payload or {})
            logger.info("proxied httpbin POST status=%d", resp.status_code)
            return resp.json()
    except Exception as e:
        logger.error("httpbin proxy POST failed: %s", e)
        raise HTTPException(status_code=503, detail=f"httpbin unavailable: {e}")


# -- composite endpoint: Redis + MySQL + httpbin in one call --

@app.get("/all")
async def all_services():
    start = time.perf_counter()
    request_counter.add(1, {"endpoint": "/all"})
    result: dict = {}
    with tracer.start_as_current_span("composite_call") as span:
        # --- Redis ---
        with tracer.start_as_current_span("redis_op") as redis_span:
            redis_span.set_attribute("db.system", "redis")
            try:
                ts = str(time.time())
                get_redis().set("demo:composite_hit", ts)
                got = get_redis().get("demo:composite_hit")
                redis_ops_total.add(1, {"operation": "set"})
                redis_ops_total.add(1, {"operation": "get"})
                redis_span.set_attribute("db.redis.success", True)
                result["redis"] = {"ok": True, "value": got}
            except Exception as e:
                redis_span.set_status(Status(StatusCode.ERROR, str(e)))
                result["redis"] = {"ok": False, "error": str(e)}

        # --- MySQL ---
        try:
            rows = await asyncio.to_thread(_query_users)
            result["mysql"] = {"ok": True, "user_count": len(rows)}
        except Exception as e:
            result["mysql"] = {"ok": False, "error": str(e)}

        # --- httpbin ---
        with tracer.start_as_current_span("httpbin_op") as httpbin_span:
            httpbin_span.set_attribute("http.url", f"{HTTPBIN_URL}/uuid")
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{HTTPBIN_URL}/uuid")
                    httpbin_requests_total.add(1, {"endpoint": "/uuid"})
                    httpbin_span.set_attribute("http.status_code", resp.status_code)
                    result["httpbin"] = {"ok": True, "uuid": resp.json().get("uuid")}
            except Exception as e:
                httpbin_span.set_status(Status(StatusCode.ERROR, str(e)))
                result["httpbin"] = {"ok": False, "error": str(e)}

    span.set_attribute("composite.all_ok", all(v.get("ok") for v in result.values()))
    duration_ms = (time.perf_counter() - start) * 1000
    request_duration.record(duration_ms, {"endpoint": "/all"})
    logger.info("composite call redis=%s mysql=%s httpbin=%s duration=%.2fms",
                result["redis"]["ok"], result["mysql"]["ok"], result["httpbin"]["ok"], duration_ms)
    return result
