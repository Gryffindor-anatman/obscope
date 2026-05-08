# 0003. Layered structure for `app/`

Status: Accepted
Date:   2026-05-08

## Context

`app/main.py` had grown to 481 lines holding env parsing, lifespan,
SQLAlchemy engine setup, pool/statement metric registration, schema
bootstrap, repository functions, all five backends' route handlers, the
composite `/all` orchestration, and a background heartbeat. Every new
endpoint required scrolling past unrelated concerns to find the right
splice point. The MySQL section alone was already a copy-paste of the
same try/except/HTTPException template repeated four times.

The repo is meant to be **navigated by agents** — both human and the
Claude-driven loop — so flat-file growth is more expensive here than in a
typical service: an agent's context window pays for every byte it reads
to find a thing. ADR 0002 commits us to keeping the *runtime* simple
(visibility over resilience), but says nothing about source layout.

## Decision

Split `app/` into five layers plus four top-level files:

```
controllers/  request/response shape, HTTPException mapping
services/     business logic, tracing, INFO/ERROR logs, custom counters
repositories/ parameterised SQL only
schemas/      pydantic DTOs
infra/        engines & clients (db.py, redis.py, httpbin.py); pool/statement metrics live here
main.py       FastAPI assembly + obs.init + lifespan + include_router
config.py     env-var Settings singleton
metrics.py    shared meter + generic counters/histograms
background.py long-running asyncio tasks
```

Discipline:

- **Controllers must not import `infra/`** — go through a service.
  (Exception: `infra.db.pool_status` for `/debug/pool` since it's pure
  introspection with no business meaning.)
- **Services raise raw exceptions**; controllers translate to
  `HTTPException`. This keeps the service callable from `composite_service`
  without losing structured failure info.
- **Repositories are pure SQL** — no spans, no logs. SQLAlchemy's
  auto-instrumentor handles spans; `infra/db.py`'s SQLAlchemy event hooks
  handle the statement histograms.
- **Module-level `logging.getLogger(__name__)` everywhere** — this makes
  log records' `scope.name` resolve to e.g. `services.redis_service` or
  `controllers.health`, giving free per-module filtering in
  VictoriaLogs without any extra config.

## Consequences

**Easier**

- Adding an endpoint is now "edit one file per layer" with predictable
  file names — no scrolling through unrelated code.
- `query_logs '...| stats by ("scope.name") count() ...'` slices logs by
  module for free; previously every log line was scoped to `app`.
- Copy-pasting `app/` into a new layered service is a clear template;
  ADR 0001's wiring snippet now has a concrete home.
- Each layer's responsibility is small enough to hold in head — easier
  to spot when a controller is doing too much.

**Harder / traps**

- 24 source files vs. 1. For trivial demo edits this is more navigation,
  not less.
- `obs.init()` **must** run before any module that calls
  `metrics.get_meter()` at import time; `main.py` enforces this by
  importing `controllers.*` *after* the `obs.init(app, ...)` line. A
  naive top-of-file import will silently get a no-op meter.
- `controllers/redis.py` and `infra/redis.py` shadow the pip `redis`
  package name lexically; works because they're only ever referenced as
  fully-qualified `controllers.redis` / `infra.redis`. Renaming either
  to bare `redis` at top-level would break.
- Circular-import risk: services may not import controllers; controllers
  may not import each other. Composite logic lives in
  `services/composite_service.py`.

## Considered and rejected

| Layout | Why rejected |
|---|---|
| Stay single-file | Status quo. Already painful at 481 lines; trajectory is worse. |
| Just `api/` + `db/` (the "lightweight" option) | Half-measure: still bundles env, metrics, lifespan, background, and httpbin client into `main.py`. Doesn't solve the "where does this go" question for non-DB code. |
| Feature folders (`users/`, `redis/`, `httpbin/` each containing controller + service + repo) | Cross-cutting changes (e.g. "every service should now record `db.system` on its span") become a grep across N folders. Layer slicing keeps cross-cutting concerns aligned by layer. |
| `src/` layout with `app` as a package | Adds Python packaging complexity without payoff — the container's `WORKDIR` is `/app` and uvicorn imports `main:app` from cwd. A package layout would require `PYTHONPATH` gymnastics for no observable benefit. |
| Inject dependencies via FastAPI `Depends(get_engine)` | Testability gain doesn't apply (no tests yet); explicit `get_engine()` calls in repositories are easier to read at a glance. Revisit if/when a test suite shows up. |

## Relationship to ADR 0002

Layered code does **not** add resilience — there's no new retry, no
circuit breaker, no swallowed exception. Every failure that was visible
before is still visible now, just emitted from a more specific
`scope.name`. The two ADRs operate on independent axes: 0002 governs
runtime behaviour, 0003 governs source layout.
