# Project: agent-driven observability stack

This is a self-contained reproduction of the architecture diagram showing
`APP → Vector → (VictoriaLogs / VictoriaMetrics / VictoriaTraces) → CODEX`.
Six services in `docker-compose.yml` (two demo apps, vector, three Victoria
backends) plus a Python MCP server in `mcp_server/` that exposes the three
query APIs to Claude Code as tools.

## Layout

```
app/                  FastAPI demo service (the "APP" being observed)
test-app/             Second FastAPI service — calls demo-api/work to demo
                      cross-service trace propagation (W3C traceparent)
obs/                  Reusable OTel bootstrap package — `obs.init(app, ...)`
                      sets up traces+logs+metrics+instrumentors in one call
vector/vector.yaml    Vector 0.55 config — single OTLP source, three OTLP sinks
docker-compose.yml    6 services: app, test-app, vector, victoria-{logs,metrics,traces}
mcp_server/           Python MCP server (stdio) → loaded via .mcp.json
.mcp.json             Wires the MCP server into Claude Code (project scope)
docs/adr/             Architecture Decision Records — one file per non-obvious
                      tech choice (e.g. `0001-sqlalchemy-over-pymysql.md`).
                      Read these before assuming a stack choice was arbitrary.
```

## What flows where

All three signals share one OTLP HTTP transport (`vector:4318`) and use the
matching Victoria* OTLP ingestion endpoints — symmetric pipeline, no custom
HTTP paths or scraping anymore.

- **Logs**:    app → `OTLP HTTP vector:4318/v1/logs`    → VictoriaLogs    `/insert/opentelemetry/v1/logs?_stream_fields=service.name,severity_text,scope.name`
- **Metrics**: app → `OTLP HTTP vector:4318/v1/metrics` → VictoriaMetrics `/opentelemetry/v1/metrics` (push every 5s via `PeriodicExportingMetricReader`)
- **Traces**:  app → `OTLP HTTP vector:4318/v1/traces`  → VictoriaTraces  `/insert/opentelemetry/v1/traces`

Vector decodes each signal as raw OTLP and re-encodes via its `opentelemetry`
sink with `codec: otlp` — no VRL transforms, no custom JSON schema.

OpenTelemetry's `LoggingHandler` (configured by `obs.init`) auto-injects
`trace_id` / `span_id` into every log record — the **cross-signal correlation
key**. Trace context propagates across services via the W3C `traceparent`
header injected by `HTTPXClientInstrumentor`, so a request from test-app to
demo-api shares one trace_id.

## App-emitted custom metrics

Beyond what auto-instrumentors emit, `app/main.py` publishes:

| Metric | Type | Labels | Source |
|---|---|---|---|
| `app_requests_total` | counter | `endpoint` | manual `.add()` per route |
| `app_request_duration_ms` | histogram | `endpoint` | manual `.record()` |
| `redis_ops_total` | counter | `operation` | manual |
| `httpbin_requests_total` | counter | `endpoint` | manual |
| `db_pool_connections` | observable gauge | `state` (idle/in_use/overflow) | `pool.checkedin/out/overflow()` callback |
| `db_statement_duration_ms` | histogram | `statement`, `operation` | SQLAlchemy `before/after_cursor_execute` events |
| `db_statement_errors_total` | counter | `statement`, `operation`, `exception_type` | SQLAlchemy `handle_error` event |

The `db_statement_*` pair is the **SQL spanmetrics** layer — same shape as
OTel Collector's `spanmetricsconnector` would derive from spans, but emitted
in-app for cheap retention. SQL is parameterised by SQLAlchemy
(`%(name)s`), so cardinality of `statement` is bounded by the number of
distinct SQL templates in code (~10), not by parameter values. The
`statement` label is whitespace-collapsed and truncated at 200 chars
defensively. See `_register_statement_metrics` in `app/main.py`.

Canonical PromQL for SQL insights:

```promql
# Most-executed SQL
topk(5, sum by (statement) (db_statement_duration_ms_count))

# Highest total DB time (the "fix this first" list)
topk(5, sum by (statement) (db_statement_duration_ms_sum))

# Slowest p95 (use rate version once data has ≥2 samples)
topk(5, histogram_quantile(0.95,
    sum by (statement, le) (rate(db_statement_duration_ms_bucket[5m]))))

# Pool state (instant)
db_pool_connections
```

Two demo-only HTTP endpoints exist to exercise these:
- `GET /debug/pool` — returns the live `pool.status()`. Use to ground-truth
  the `db_pool_connections` gauge against in-process state.
- `GET /mysql/slow?seconds=N` — runs `SELECT SLEEP(N)`, holding a
  connection across an OTLP export tick. Run several in parallel to make
  `db_pool_connections{state="in_use"}` visibly rise in vmui.

## Adding a new app

1. New service folder with `main.py`, `requirements.txt`, `Dockerfile`
   (mirror `test-app/` — Dockerfile build context is repo root, copies
   `obs/` and pip-installs it).
2. In `main.py`: `import obs; obs.init(app, service_name="...")` — that's it.
3. In `docker-compose.yml`: env vars `OTEL_SERVICE_NAME` and
   `OTEL_EXPORTER_OTLP_ENDPOINT=http://vector:4318`.

`vector/vector.yaml` and the Victoria backends require zero changes.

## MCP tools (8)

Loaded as `observability` MCP server. Three query backends + workflow helpers:

| Tool | Backend | Purpose |
|---|---|---|
| `query_logs(query, limit)` | VictoriaLogs | LogsQL — `level:ERROR _time:5m` |
| `query_metrics(query, range, step)` | VictoriaMetrics | PromQL instant or range |
| `list_metric_names(match_regex)` | VictoriaMetrics | Discovery |
| `get_trace(trace_id)` | VictoriaTraces | Lookup by 32-hex ID |
| `search_traces(service, operation, lookback, limit, min_duration_ms)` | VictoriaTraces | Jaeger search |
| `list_services()` | VictoriaTraces | Discovery |
| `restart_app(rebuild, timeout_s)` | docker | Restart, poll /health |
| `run_workload(profile, requests, concurrency)` | http | Generate traffic; returns `[start, end]` window |

## Standard closed-loop workflow

When debugging or verifying a change, follow this loop. **Do not skip steps** —
each one disambiguates a different failure mode.

1. **Observe** — what's actually happening?
   - For a complaint about errors: `query_logs('level:ERROR _time:30m')`
   - For latency: `query_metrics('histogram_quantile(0.95, sum by (le) (rate(app_request_duration_ms_bucket[5m])))', range='30m')`
   - Pick a representative `trace_id` from a log → `get_trace(...)` to see the call chain.

2. **Hypothesize** — propose a single change. State it explicitly before editing.

3. **Patch** — `Edit` / `Write` files in `app/` (or wherever).

4. **Restart** — `restart_app(rebuild=True)` for **any** edit under `app/`
   (the container has no bind mount, so code is baked in at build time).
   `restart_app(rebuild=False)` only restarts the existing image — useful
   for picking up env-var changes in `docker-compose.yml`, nothing else.

5. **Re-run workload** — `run_workload(profile=..., requests=...)`.
   Capture the returned `started_at` / `ended_at`.

6. **Verify** — query bounded by the workload window:
   - `query_logs(f'level:ERROR _time:[{started_at:.0f}, {ended_at:.0f}]')`
   - `query_metrics('app_requests_total')` — instant value of the counter
   - `search_traces(service='demo-api', lookback='2m', min_duration_ms=...)`

   **Important**: counters reset to 0 on restart. `rate()` / `increase()` need
   ≥2 OTLP export samples (≥10s post-restart, default export interval is 5s)
   to return non-zero. For fast feedback, prefer instant queries on raw counters
   and compare against the workload's reported `responses` dict.

7. **Decide** — fixed? regression? loop again from step 1 with new evidence.

## Workload profiles

- `normal` — all `/work` (200)
- `errors` — all `/boom` (500 + ERROR log + exception span)
- `mixed`  — ~80/20 work/boom (default; good for general regression checks)
- `load`   — parallel `/work` for throughput / latency tests

## Anti-patterns to avoid

- **Docker Hub is blocked without proxy** — Docker Desktop's GUI proxy at
  `127.0.0.1:7897` doesn't work because that's the VM's loopback, and
  `host.docker.internal` doesn't resolve from buildkit's sandbox. Until the
  Mac's LAN IP is wired into Docker Desktop, builds must be done from a shell
  with proxy env vars exported, e.g.:
  ```
  export http_proxy=http://127.0.0.1:7897 https_proxy=http://127.0.0.1:7897 \
         HTTP_PROXY=http://127.0.0.1:7897 HTTPS_PROXY=http://127.0.0.1:7897
  docker compose up --build -d app
  ```
  MCP `restart_app(rebuild=True)` will fail in this state because it shells out
  to `docker compose` without the env. Use the shell command instead.

- **Don't restart for changes that don't need it.** Editing `vector/vector.yaml`
  needs `docker compose restart vector`, not `restart_app`. Editing
  `mcp_server/` needs the user to restart Claude Code.
- **Don't query metrics within the first ~10s of a restart.** Counters are at
  0 and rate() returns no data. Either sleep, or use logs/traces for fast loops.
- **Don't assume a trace_id will resolve.** `get_trace()` may return empty if
  the span hasn't been ingested yet (BatchSpanProcessor flush delay ~5s).
- **Don't generate traffic with raw `curl` in a loop** when `run_workload`
  exists — you lose the structured `[start, end]` window for verification.

- **`restart_app(rebuild=False)` does NOT pick up `app/*.py` edits.** There
  is no bind mount on the `app` service in `docker-compose.yml` — code is
  `COPY`-ed into the image at build time. After editing any file under
  `app/`, use `restart_app(rebuild=True)`. Symptom of getting this wrong:
  the new behaviour silently doesn't appear, no error, the old container
  just keeps running the old code. If iteration speed matters, add a
  `volumes: - ./app:/app` mount to the service and remember to
  `--reload` uvicorn — but neither is set up today.
