# Agent-driven observability stack

A self-contained reproduction of the architecture below. Five Docker
containers carry telemetry from a demo FastAPI app into a Victoria backend
trio (logs / metrics / traces); a Python MCP server exposes the three
query APIs to Claude Code so the AI can observe → hypothesize → patch →
restart → re-verify in one loop.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  采集 (Collection)        │  存储 (Storage)         │  Agent           │
├─────────────────────────────────────────────────────────────────────────┤
│  demo-app ─OTLP──▶ vector ─opentelemetry sink──▶ VictoriaTraces  ◀─┐  │
│  demo-app ─HTTP──▶ vector ─http sink───────────▶ VictoriaLogs    ◀─┤  │
│  vector ─scrape──▶ /metrics, ─remote_write─────▶ VictoriaMetrics ◀─┤  │
│                                                                       │ │
└────────────────────────────────────────────────────┐                  │ │
                                                     │                  │ │
                                            ┌────────▼──────┐   8 MCP  │ │
                                            │  Claude Code  │   tools  │ │
                                            │  (agent loop) ├──────────┘ │
                                            └────┬──────────┘            │
                                       Edit/Write│                       │
                                       restart ──┴──▶ rebuilds app ─────┘
                                       run_workload  generates traffic
```

## File layout

```
.
├── README.md              ← this file (operator instructions)
├── CLAUDE.md              ← agent-facing playbook (auto-loaded by Claude Code)
├── docker-compose.yml     ← 5 services + 3 named volumes
├── app/                   ← FastAPI demo service
│   ├── main.py            ←   3 endpoints + OTel instrumentation + log shipper
│   ├── requirements.txt
│   └── Dockerfile
├── vector/
│   └── vector.yaml        ← Vector 0.55 config: 3 sources, 3 sinks
├── mcp_server/            ← Python MCP server exposed to Claude Code
│   ├── pyproject.toml
│   └── obs_mcp/__main__.py  ← 8 tools (6 query + 2 loop-closure)
└── .mcp.json              ← project-scope MCP config (Claude Code reads this)
```

## Quick start

```bash
cd /Users/cguo/code/empty

# 1. Bring up the 5-container stack (~30s on first run, pulls 3 Victoria images)
docker compose up --build -d
docker compose ps   # all 5 should be Up

# 2. (Once) Install MCP server deps
cd mcp_server && uv sync && cd ..

# 3. Restart Claude Code to load .mcp.json (no other way to register MCP)
#    Then in the new session:
#      /mcp        → should show "observability · connected · 8 tools"
```

## What's running

| Service | Image | Port(s) | Role |
|---|---|---|---|
| `demo-app` | built from `./app` | 8000 | FastAPI; emits OTLP traces + HTTP logs + Prom metrics |
| `vector` | timberio/vector:0.55.0-debian | 4317/4318 (OTLP), 9880 (logs), 8686 (API) | Receive + fan-out |
| `victoria-logs` | victoriametrics/victoria-logs:v1.50.0 | 9428 | LogsQL store |
| `victoria-metrics` | victoriametrics/victoria-metrics:v1.108.1 | 8428 | PromQL store |
| `victoria-traces` | victoriametrics/victoria-traces:latest | 10428 | TraceQL/Jaeger store |

App endpoints (port 8000):
- `GET /health` — liveness
- `GET /work` — normal path; emits INFO log + `do_work` span + counter+1
- `GET /boom` — error path; emits ERROR log + 500 + exception span
- `GET /metrics/` — Prometheus exposition (note trailing slash)

## Verifying each layer with curl

### Layer 1 — collection (just the Vector pipeline)

```bash
# Generate something
for i in 1 2 3; do curl -s localhost:8000/work > /dev/null; done
curl -s -o /dev/null localhost:8000/boom

# Vector API health
curl -s localhost:8686/health
```

### Layer 2 — storage (each Victoria backend)

```bash
# VictoriaLogs — most recent ERRORs in last 30m, with trace_id
curl -s 'http://localhost:9428/select/logsql/query' \
  --data-urlencode 'query=level:ERROR _time:30m' \
  | jq -c '{t:._time, msg:._msg, trace:.trace_id}'

# VictoriaMetrics — current request counts
curl -s 'http://localhost:8428/api/v1/query?query=app_requests_total' \
  | jq '.data.result[] | {ep: .metric.exported_endpoint, val: .value[1]}'

# VictoriaTraces — services seen, then most recent traces
curl -s 'http://localhost:10428/select/jaeger/api/services' | jq .
curl -s 'http://localhost:10428/select/jaeger/api/traces?service=demo-api&limit=3' \
  | jq '.data[] | {traceID, ops:[.spans[].operationName]}'
```

### Cross-signal correlation (the whole point)

```bash
curl -s -o /dev/null localhost:8000/boom
sleep 3   # wait for log + span to ship

# Pull trace_id from the latest ERROR, then look up the trace
TRACE_ID=$(curl -s 'http://localhost:9428/select/logsql/query' \
  --data-urlencode 'query=level:ERROR _time:30s | head 1' \
  | jq -r '.trace_id')
echo "trace_id = $TRACE_ID"

curl -s "http://localhost:10428/select/jaeger/api/traces/$TRACE_ID" \
  | jq '.data[0].spans[] | {op: .operationName, dur_us: .duration}'
```

## Layer 3 — using the agent (Claude Code)

After the MCP server is loaded (`/mcp` shows `connected · 8 tools`), ask
Claude Code natural-language questions. It will pick the right LogsQL /
PromQL / TraceQL behind the scenes:

```
"现在 /boom 的错误率是多少？"
"找最近一条 ERROR 日志，把对应的完整调用链拉出来分析"
"/work 的 P95 延迟最近 30 分钟有没有变化？"
"最近一小时有没有耗时超过 100ms 的请求？是哪些？"
```

For the **full closed loop** (change → restart → verify), give it a task that
spans steps:

```
"把 /work 端点的随机延迟范围从 0.02-0.15s 改成 0.5-1.5s，
 重启 app 后跑 mixed workload，确认 P95 延迟确实变高了"

"在 app 里加一个 /sleep 端点，sleep 2 秒后返回。
 rebuild 容器，跑 load 模式 10 个请求，
 确认它在 metrics 里出现且 P95 接近 2s"
```

The agent uses `Edit` (built-in) → `restart_app` → `run_workload` →
`query_metrics` / `query_logs` / `search_traces` to close the loop on its own.

## Common ops

```bash
# Generate traffic manually (without the MCP run_workload tool)
for i in {1..30}; do curl -s localhost:8000/work > /dev/null & done; wait

# Restart only one component
docker compose restart app          # after editing app/main.py
docker compose restart vector       # after editing vector/vector.yaml
docker compose up --build -d app    # after editing requirements.txt or Dockerfile

# Tail Vector internals (filter to one signal type)
docker logs -f vector 2>&1 | grep --line-buffered '"logger":"app"'         # app logs only
docker logs -f vector 2>&1 | grep --line-buffered '"name":"app_'            # app metrics only

# Stop everything (keeps volumes — ingested data persists)
docker compose down

# Stop AND delete all stored telemetry
docker compose down -v
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/mcp` doesn't show `observability` | Claude Code session not restarted, or `.mcp.json` not trusted | Quit and relaunch Claude Code; confirm trust prompt |
| MCP tools fail with `Connection refused` | Stack not running | `docker compose ps` — restart whatever is down |
| Logs/metrics empty just after restart | Counters reset; scrape interval is 5s | Wait ~10s; query raw counters not `rate()` |
| `app /metrics` returns 307 | Trailing slash — Vector hits `/metrics`, FastAPI mount serves at `/metrics/` | Vector config already uses `/metrics/`; if you `curl` manually, use `-L` |
| `vector` container exits with `unknown field` | Config schema changed between Vector versions | Pin image tag in compose; cross-reference `https://vector.dev/docs/reference/configuration/sinks/` |
| Stale spans tracing the `/metrics` scrape | Self-feedback (FastAPI auto-instruments the scraper) | `excluded_urls="/metrics,/metrics/,/health"` already passed to `FastAPIInstrumentor` |
| App log feedback loop (`POST /logs ... 200 OK` repeating) | `httpx` instrumentation logs every shipping POST | `logging.getLogger("httpx").setLevel(WARNING)` already set |

## Architecture decisions worth knowing

- **Why `service.name` is set on the OTel resource, not as a label** — so it
  flows through to all three signal types under one name (visible in
  VictoriaMetrics as `service_name` label, in spans as resource attribute, in
  logs as a custom field).
- **Why metrics use Prometheus pull, not OTLP push** — Vector 0.43's OTLP
  source had no metrics output; we kept Prom scrape after upgrading to 0.55
  because VictoriaMetrics is natively Prom-compatible and the path is more
  observable.
- **Why traces use `use_otlp_decoding.traces: true` + `codec: otlp`** — keeps
  the OTLP protobuf payload intact through Vector for byte-equivalent forward.
  Without it, you'd have to hand-craft `resourceSpans` JSON in VRL.
- **Why the MCP server is Python** — the only other code in the project is
  Python (FastAPI app); FastMCP is the most ergonomic stdio MCP framework.
- **Why CLAUDE.md is separate from this README** — different audiences. The AI
  reads CLAUDE.md every session and needs terse rules; humans read README once
  and need context.
