# Project: agent-driven observability stack (infra-only)

This repo is the **infrastructure half** of an OTel-based observability lab:

```
APP → Vector → (VictoriaLogs / VictoriaMetrics / VictoriaTraces) → CODEX
```

The applications being observed (`app1`, `app2`) used to live under
`demo-app/` here, but were extracted into their own repos:

- `/Users/cguo/code/app1` — multi-backend FastAPI gateway (MySQL + Redis + httpbin)
- `/Users/cguo/code/app2` — auth + topic UI FastAPI service

Both connect back to this stack via `host.docker.internal:<port>`. This
repo no longer builds or ships any application code; its job is
1. running the observability backends, 2. serving the shared `obs` Python
package over a private PyPI, and 3. exposing query MCP tools to Claude Code.

## Layout

```
docker-compose.yml      6 services: vector, victoria-{logs,metrics,traces}, redis, pypi
vector/vector.yaml      Vector 0.55 config — single OTLP source, three OTLP sinks
obs/                    Reusable OTel bootstrap package — published to local pypi
                        as obs==0.1.0 (built once via `python -m build` + twine upload)
pypi-packages/          pypiserver storage (gitignored)
mcp_server/             Python MCP server (stdio) — observability query tools
mcp_server_db/          Python MCP server (stdio) — MySQL + Redis tools
.mcp.json               Wires both MCP servers into Claude Code (project scope)
docs/adr/               ADRs — read these before assuming a tech choice was arbitrary
demo-app.bak/           Soft-deleted previous home of app1/app2; gitignored, safe to rm later
```

## What this stack accepts

All three OTel signals share one transport (`vector:4318` from inside the
docker network, `host.docker.internal:4318` from sibling-repo containers).

- **Logs**:    `OTLP HTTP /v1/logs`    → VictoriaLogs    `/insert/opentelemetry/v1/logs?_stream_fields=service.name,severity_text,scope.name`
- **Metrics**: `OTLP HTTP /v1/metrics` → VictoriaMetrics `/opentelemetry/v1/metrics`
- **Traces**:  `OTLP HTTP /v1/traces`  → VictoriaTraces  `/insert/opentelemetry/v1/traces`

Vector decodes each signal as raw OTLP and re-encodes via its `opentelemetry`
sink with `codec: otlp` — no VRL transforms, no custom JSON schema.

## Ports exposed on the host

| Port | Service | Purpose |
|---|---|---|
| 4317 | vector | OTLP gRPC |
| 4318 | vector | OTLP HTTP (logs + metrics + traces) |
| 8686 | vector | Vector API |
| 9428 | victoria-logs | LogsQL HTTP API |
| 8428 | victoria-metrics | PromQL HTTP API |
| 10428 | victoria-traces | TraceQL / Jaeger HTTP API |
| 6379 | redis | shared cache for all apps |
| 8080 | pypi | private package index (obs hosted here) |

App containers in sibling repos reach all of these via
`host.docker.internal:<port>` and declare
`extra_hosts: ["host.docker.internal:host-gateway"]` for Linux compat.

## The `obs` package and the local PyPI

`obs/` is the shared OTel bootstrap (`obs.init(app, service_name="...")`
sets up traces+logs+metrics+instrumentors in one call). Apps consume it as
a versioned dependency, **not as a path/COPY** — that's what made the
original colocation rigid.

To publish a new version:

```bash
cd obs/
# bump version in pyproject.toml
rm -rf dist build *.egg-info
python -m build --wheel --sdist
twine upload --repository-url http://localhost:8080 --username '' --password '' dist/*
```

Apps install via:

```
pip install --extra-index-url http://host.docker.internal:8080/simple/ \
    --trusted-host host.docker.internal \
    obs==0.1.0
```

The `--trusted-host` flag is required because the index is plain HTTP.

## MCP tools (24 — 7 observability + 17 database)

### Observability (`observability` MCP server, `mcp_server/`)

Three query backends + workload helper. `restart_app` was removed when
apps moved out — each app repo provides its own restart command.

| Tool | Backend | Purpose |
|---|---|---|
| `query_logs(query, limit)` | VictoriaLogs | LogsQL — `level:ERROR _time:5m` |
| `query_metrics(query, range, step)` | VictoriaMetrics | PromQL instant or range |
| `list_metric_names(match_regex)` | VictoriaMetrics | Discovery |
| `get_trace(trace_id)` | VictoriaTraces | Lookup by 32-hex ID |
| `search_traces(service, operation, lookback, limit, min_duration_ms)` | VictoriaTraces | Jaeger search |
| `list_services()` | VictoriaTraces | Discovery |
| `run_workload(profile, requests, concurrency)` | http | Generate traffic against `APP_URL` (default `http://localhost:8000`); returns `[start, end]` window |

`run_workload` still targets `APP_URL` (default `localhost:8000` → app1).
Override with `APP_URL=http://localhost:8001` for app2.

### Database (`database` MCP server, `mcp_server_db/`)

MySQL (5 tools) + Redis (12 tools). Connects to local MySQL/Redis via env
vars (defaults: `127.0.0.1:3306` / `127.0.0.1:6379`). See
`mcp_server_db/` source for the full list.

## Adding a new app

1. New repo somewhere, mirror `app1`'s shape: own `Dockerfile`,
   `docker-compose.yml`, `requirements.txt` listing `obs==<version>`.
2. Dockerfile installs deps with
   `--extra-index-url http://host.docker.internal:8080/simple/ --trusted-host host.docker.internal`.
3. Compose env: `OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318`,
   plus `extra_hosts: ["host.docker.internal:host-gateway"]`.
4. In `main.py`: `import obs; obs.init(app, service_name="...")`.

This stack requires zero changes for a new consumer.

## Anti-patterns to avoid

- **Docker Hub is blocked without proxy.** Docker Desktop's GUI proxy at
  `127.0.0.1:7897` doesn't work because that's the VM's loopback, and
  `host.docker.internal` doesn't resolve from buildkit's sandbox. Builds
  and pulls must be done from a shell with proxy env vars exported:
  ```
  export http_proxy=http://127.0.0.1:7897 https_proxy=http://127.0.0.1:7897 \
         HTTP_PROXY=http://127.0.0.1:7897 HTTPS_PROXY=http://127.0.0.1:7897
  docker compose up --build -d
  ```
- **Don't restart for changes that don't need it.** Editing
  `vector/vector.yaml` needs `docker compose restart vector` only.
  Editing `mcp_server/` needs the user to restart Claude Code.
- **Don't query metrics within the first ~10s of a restart.** Counters are
  at 0 and `rate()` returns no data. Either wait, or use logs/traces.
- **Don't assume a `trace_id` will resolve immediately.** `get_trace()`
  may return empty if the span hasn't been ingested yet
  (BatchSpanProcessor flush delay ~5s).
- **Don't generate traffic with raw `curl` in a loop** when `run_workload`
  exists — you lose the structured `[start, end]` window for verification.

## Standard query workflow

For agent-driven debugging across the stack:

1. Look at the symptom — `query_logs('level:ERROR _time:30m')` for errors,
   or `query_metrics(...)` for latency.
2. Pick a representative `trace_id` from a log → `get_trace(...)` to see
   the call chain.
3. (Edit the app in its own repo, restart it there.)
4. Re-run workload → query bounded by `[started_at, ended_at]` window.

App-side code edits, restarts, and app-specific metrics are documented in
each app's CLAUDE.md.
