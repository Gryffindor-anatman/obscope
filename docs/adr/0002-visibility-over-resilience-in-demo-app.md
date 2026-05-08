# 0002. Visibility over resilience in the demo app

Status: Accepted
Date:   2026-05-08

## Context

`app/` exists to be **observed**, not to be production-grade. The whole repo
is a closed-loop demo of `APP → Vector → Victoria* → CODEX`: code emits
signals, the MCP tools query them, an agent diagnoses. Every hardening
pattern that silently absorbs a failure (retry, fallback, swallowed
exception, defensive lock) removes a failure mode the agent was supposed to
see — i.e. it breaks the demo before it breaks production.

A code review on 2026-05-08 surfaced ~9 "issues" against `app/main.py`. Some
were real bugs (URL-unencoded password, no pool metrics) and got fixed.
Several others were idiomatic backend-hardening advice that, while correct
in a production app, work *against* the purpose of this one. Without a
written record, those suggestions will be re-litigated every time a new
reviewer reads the file.

## Decision

When evaluating a code change to `app/`, prefer the option that produces
**more observable signal** over the one that produces **fewer user-visible
failures**. Hardening that hides failures is rejected by default; if it must
be added (e.g. for an explicit demo of resilience patterns), the rationale
goes in a follow-up ADR.

The same principle motivated adding `db_pool_connections` (gauge for
`idle / in_use / overflow`) in this same change set: pool exhaustion is
exactly the kind of failure the demo should make visible, not paper over.

## Consequences

**Easier**

- New endpoints can keep the trivial `try/except → 503` shape; no need to
  argue about retry policies, circuit breakers, or backoff.
- Reviewers have a single answer to point at when "you should add X" comes
  up for the Nth time.
- Failure injection is cheap — restart a backend, hit `/all`, every layer
  of the failure shows up in logs / traces / metrics.

**Harder / traps**

- Anyone copying patterns from `app/` into a real service must consciously
  re-add resilience. The wiring guide in ADR 0001 is **not** a production
  template.
- Some bug classes that production code would mask (transient connection
  failures, races on lazy globals) are observable here as user-facing 503s.
  That is the intended behaviour, not a regression to fix.

## Considered and rejected

Each row was raised in the 2026-05-08 review and explicitly declined under
this ADR. Recorded so they don't have to be re-argued.

| Suggestion | Why rejected |
|---|---|
| Wrap lazy `_engine` / `_redis_pool` init in a `threading.Lock` | Lifespan startup eagerly initialises both via `init_mysql_schema()`; the race window is closed before any request arrives. Adding a lock would suggest the pattern is reusable as-is, which it isn't. |
| Call `_engine.dispose()` in lifespan shutdown | Container exit closes the sockets; MySQL reaps them via `wait_timeout`. Adding it implies orderly shutdown matters here — it doesn't. |
| Replace `pool_pre_ping=True` with `pool_recycle=...` for throughput | `pool_pre_ping` makes stale-connection failures *visible as a recovered checkout span*; recycling silently rotates connections on a timer. Visibility wins. |
| Add `tenacity` retries around DB / Redis / httpx calls | Retries hide transient failures from logs and traces. The whole point is to surface them to `query_logs` / `search_traces`. |
| Dedicated `ThreadPoolExecutor` for DB work | Default executor caps at `min(32, cpu+4)`; pool caps at `pool_size + max_overflow = 10`. The DB pool saturates first, so a separate executor changes nothing measurable. |
