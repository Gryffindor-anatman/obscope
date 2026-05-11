# Agent-driven observability stack (infra)

The infrastructure half of an OTel-based observability lab. Hosts the
collection + storage layer plus a private PyPI for the shared `obs`
bootstrap package. Applications being observed live in **separate
sibling repos** and connect via `host.docker.internal:<port>`.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Apps (sibling repos)        │  Collection + Storage      │  Agent     │
├─────────────────────────────────────────────────────────────────────────┤
│  app1 / app2 ─OTLP HTTP─▶ vector ─OTLP─▶ VictoriaTraces   ◀─┐          │
│                                  ─OTLP─▶ VictoriaLogs     ◀─┤          │
│                                  ─OTLP─▶ VictoriaMetrics  ◀─┤          │
│                                                              │          │
│                                                     ┌────────▼──────┐  │
│                                                     │  Claude Code  │  │
│                                              7 obs  │  (agent loop) │  │
│                                              17 db  └───────────────┘  │
│                                              tools                     │
└─────────────────────────────────────────────────────────────────────────┘
```

Sibling app repos:
- `/Users/cguo/code/app1` — multi-backend FastAPI gateway
- `/Users/cguo/code/app2` — auth + topic UI FastAPI service

## File layout

```
.
├── README.md             ← this file
├── CLAUDE.md             ← agent playbook (auto-loaded by Claude Code)
├── docker-compose.yml    ← 6 infra services + 3 named volumes
├── obs/                  ← shared OTel bootstrap, published to local pypi
├── pypi-packages/        ← pypiserver storage (gitignored)
├── vector/vector.yaml    ← Vector 0.55 OTLP-in / OTLP-out config
├── mcp_server/           ← observability query MCP (LogsQL/PromQL/TraceQL + workload)
├── mcp_server_db/        ← MySQL + Redis MCP
├── docs/adr/             ← architecture decisions
└── .mcp.json             ← MCP wiring (Claude Code project scope)
```

## Quick start

```bash
# Docker Hub is blocked without proxy; export every shell that hits docker
export http_proxy=http://127.0.0.1:7897 https_proxy=http://127.0.0.1:7897 \
       HTTP_PROXY=http://127.0.0.1:7897 HTTPS_PROXY=http://127.0.0.1:7897

# 1. Bring up the 6-container infra stack (~30s on first run)
docker compose up -d
docker compose ps

# 2. (Once) Install MCP server deps
cd mcp_server && uv sync && cd ..
cd mcp_server_db && uv sync && cd ..

# 3. (Once) Publish obs to the local pypi so apps can pip install it
cd obs && python -m build --wheel --sdist
twine upload --repository-url http://localhost:8080 --username '' --password '' dist/*
cd ..

# 4. Restart Claude Code so it loads .mcp.json
#    /mcp should show "observability · connected · 7 tools"
#                     "database · connected · 17 tools"
```

Then bring up apps in their own repos (`docker compose up --build -d`).
Each repo has its own README.

## Ports

| Port | Service | Purpose |
|---|---|---|
| 4317 | vector | OTLP gRPC |
| 4318 | vector | OTLP HTTP (logs + metrics + traces) |
| 8686 | vector | Vector API |
| 9428 | victoria-logs | LogsQL |
| 8428 | victoria-metrics | PromQL |
| 10428 | victoria-traces | TraceQL / Jaeger |
| 6379 | redis | shared cache |
| 8080 | pypi | private package index |

## Verifying each layer with curl

### Collection — Vector
```bash
curl -s localhost:8686/health
```

### Storage — each Victoria backend
```bash
# Logs (last 30m of ERRORs with trace_id)
curl -s 'http://localhost:9428/select/logsql/query' \
  --data-urlencode 'query=level:ERROR _time:30m' \
  | jq -c '{t:._time, msg:._msg, trace:.trace_id}'

# Metrics (services seen)
curl -s 'http://localhost:8428/api/v1/label/service_name/values' | jq .

# Traces
curl -s 'http://localhost:10428/select/jaeger/api/services' | jq .
```

### Cross-signal (an ERROR log → its trace)
```bash
TRACE_ID=$(curl -s 'http://localhost:9428/select/logsql/query' \
  --data-urlencode 'query=level:ERROR _time:5m | head 1' \
  | jq -r '.trace_id')
curl -s "http://localhost:10428/select/jaeger/api/traces/$TRACE_ID" \
  | jq '.data[0].spans[] | {op: .operationName, dur_us: .duration}'
```

## Common ops

```bash
# Restart only one component
docker compose restart vector       # after editing vector/vector.yaml
docker compose restart pypi
docker compose up -d --force-recreate redis

# Tail vector internals
docker logs -f vector 2>&1

# Stop everything (keeps volumes — ingested data persists)
docker compose down

# Stop AND delete all stored telemetry
docker compose down -v
```

## Publishing a new `obs` version

```bash
cd obs/
# bump pyproject.toml version
rm -rf dist build *.egg-info
python -m build --wheel --sdist
twine upload --repository-url http://localhost:8080 --username '' --password '' dist/*
```

Apps then bump `obs==<new>` in their `requirements.txt` and rebuild.

## Architecture decisions worth knowing

- **`service.name` set on the OTel resource, not as a label** — flows
  through to all three signal types under one name.
- **All signals over OTLP HTTP** (no Prom scrape). Vector decodes raw
  OTLP and re-encodes via `opentelemetry` sink with `codec: otlp` —
  byte-equivalent forward, no VRL transforms.
- **Apps live in sibling repos and connect via `host.docker.internal`**
  — was an explicit decoupling decision; previously apps + infra were
  one compose project. See `docs/adr/` if a record was created.
- **`obs` is consumed as a versioned dependency**, not as a local path
  — that's what made the original colocation rigid.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/mcp` doesn't show servers | Claude Code session not restarted, or `.mcp.json` not trusted | Quit and relaunch; confirm trust prompt |
| MCP tools fail with `Connection refused` | Stack not running | `docker compose ps` |
| `docker pull` EOFs | No proxy in shell | `export http_proxy=...` (see Quick start) |
| Apps can't reach pypi at build time on Linux | `host.docker.internal` not auto-set | Add `--add-host=host.docker.internal:host-gateway` to buildx, or use the host LAN IP |
| Logs/metrics empty just after restart | Counters reset; export interval is 5s | Wait ~10s; query raw counters not `rate()` |
