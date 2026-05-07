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

4. **Restart** — `restart_app(rebuild=True)` if you changed `requirements.txt`
   or `Dockerfile`; otherwise `restart_app(rebuild=False)` is ~1s.

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

- **Docker Hub is blocked** — all `docker compose up --build -d` commands must be
  prefixed with `proxy` to route through the proxy, e.g. `proxy docker compose up --build -d`.
  Otherwise image pulls will time out.

- **Don't restart for changes that don't need it.** Editing `vector/vector.yaml`
  needs `docker compose restart vector`, not `restart_app`. Editing
  `mcp_server/` needs the user to restart Claude Code.
- **Don't query metrics within the first ~10s of a restart.** Counters are at
  0 and rate() returns no data. Either sleep, or use logs/traces for fast loops.
- **Don't assume a trace_id will resolve.** `get_trace()` may return empty if
  the span hasn't been ingested yet (BatchSpanProcessor flush delay ~5s).
- **Don't generate traffic with raw `curl` in a loop** when `run_workload`
  exists — you lose the structured `[start, end]` window for verification.
