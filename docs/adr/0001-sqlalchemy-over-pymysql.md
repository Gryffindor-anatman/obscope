# 0001. SQLAlchemy over raw pymysql

Status: Accepted
Date:   2026-05-08

## Context

`app/main.py` originally used `pymysql` directly. Each `/mysql/*` endpoint
duplicated ~15 lines of boilerplate: `tracer.start_as_current_span("mysql_*")`
plus manual `db.system` / `db.operation` / `db.sql.table` /
`db.rows_returned` attributes plus a `mysql_ops_total` counter increment plus
exception handling. The composite `/all` endpoint repeated the pattern again.
Total: roughly 60 lines of hand-written instrumentation across four call
sites. Every new query would copy the same template.

The rest of the project's instrumentation is **automatic** — `obs.init` wires
up `FastAPIInstrumentor` and `HTTPXClientInstrumentor` and we never touch
HTTP tracing again. MySQL was the odd one out.

## Decision

Switch the MySQL data layer to **SQLAlchemy 2.0 Core** (`create_engine` +
`text()`), keep `pymysql` as the DB-API driver, and rely on
`opentelemetry-instrumentation-sqlalchemy` for spans.

Calling `SQLAlchemyInstrumentor().instrument(engine=engine)` per-engine
emits both:

- a `connect` span on each pool checkout, with
  `db.system=mysql`, `db.name`, `db.user`, `net.peer.name`, `net.peer.port`
- a per-statement span (`SELECT demoapp`, `INSERT demoapp`, …) with the
  full SQL in `db.statement`

All hand-written `tracer.start_as_current_span("mysql_*")` blocks and the
`mysql_ops_total` counter were removed.

## Consequences

**Easier**

- New endpoints / queries get traces for free — just `engine.execute(...)`.
- Span attributes are now standardised OTel semantic conventions
  (`db.system`, `db.statement`, `net.peer.*`) rather than ad-hoc names.
- Connection pooling (`pool_size`, `pool_pre_ping`) comes for free; no need
  to hand-roll a connection helper.
- One consistent style for all auto-instrumented backends (FastAPI, httpx,
  SQLAlchemy).

**Harder / traps**

- The instrumentor's no-arg form `SQLAlchemyInstrumentor().instrument()`
  patches `create_engine` globally but, in practice with our lazy
  `get_engine()` pattern, **only emits `connect` spans, not statement
  spans**. The fix is to call `SQLAlchemyInstrumentor().instrument(engine=e)`
  explicitly after each `create_engine`. Don't try to centralise this in
  `obs.init` — there is no engine to bind to at init time.
- `mysql_ops_total` (Prometheus counter) is gone. Anything querying it must
  switch to deriving op counts from the trace stream
  (`db.system="mysql"` filter) or from a histogram on span duration. No
  dashboard depended on it yet, so this is fine for now.
- Slightly more startup time (engine construction + listener registration);
  unmeasurable in our setup.

## How a new MySQL-using app should be wired

```python
from sqlalchemy import create_engine, text
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

_engine = None

def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine("mysql+pymysql://…", pool_pre_ping=True)
        SQLAlchemyInstrumentor().instrument(engine=_engine)
    return _engine
```

Plus `sqlalchemy` and `pymysql` in `requirements.txt`, and
`opentelemetry-instrumentation-sqlalchemy` is already a dep of the `obs`
package.
