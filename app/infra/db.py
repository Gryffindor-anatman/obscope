import time
from urllib.parse import quote_plus

from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.metrics import Observation
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from config import settings
from metrics import meter

_engine: "Engine | None" = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = (
            f"mysql+pymysql://{settings.MYSQL_USER}:{quote_plus(settings.MYSQL_PASSWORD)}"
            f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DB}?charset=utf8mb4"
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


def init_schema() -> None:
    bootstrap_url = (
        f"mysql+pymysql://{settings.MYSQL_USER}:{quote_plus(settings.MYSQL_PASSWORD)}"
        f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/?charset=utf8mb4"
    )
    bootstrap = create_engine(bootstrap_url, connect_args={"connect_timeout": 3})
    with bootstrap.begin() as conn:
        conn.execute(text(
            f"CREATE DATABASE IF NOT EXISTS {settings.MYSQL_DB} CHARACTER SET utf8mb4"
        ))
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


def ping() -> bool:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


def sleep_query(seconds: int) -> bool:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT SLEEP(:s)"), {"s": seconds})
    return True


def pool_status() -> dict:
    p = get_engine().pool
    return {
        "status": p.status(),
        "checkedin": p.checkedin(),
        "checkedout": p.checkedout(),
        "size": p.size(),
        "overflow": p.overflow(),
    }
