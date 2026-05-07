"""MCP server exposing VictoriaLogs, VictoriaMetrics, and VictoriaTraces query APIs.

Run with: `python -m obs_mcp` (over stdio, intended for Claude Code MCP integration).
"""
import asyncio
import json
import os
import re
import time
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP

VLOGS_URL = os.getenv("VLOGS_URL", "http://localhost:9428")
VMETRICS_URL = os.getenv("VMETRICS_URL", "http://localhost:8428")
VTRACES_URL = os.getenv("VTRACES_URL", "http://localhost:10428")
APP_URL = os.getenv("APP_URL", "http://localhost:8000")
COMPOSE_DIR = os.getenv("COMPOSE_DIR", "/Users/cguo/code/empty")

mcp = FastMCP("observability")
# Targets are all on localhost — opt out of env-based proxy detection.
client = httpx.AsyncClient(timeout=15.0, trust_env=False)


def _parse_duration_seconds(s: str) -> int:
    m = re.fullmatch(r"(\d+)([smhd])", s.strip())
    if not m:
        raise ValueError(f"Invalid duration {s!r}; use e.g. '30s', '5m', '1h', '2d'")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


# ─── LogsQL ────────────────────────────────────────────────────────────────

@mcp.tool()
async def query_logs(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """Query VictoriaLogs with LogsQL.

    Logs are ingested as OTLP, so fields follow OTel naming
    (`severity_text`, `service.name`, `scope.name`, `code.filepath`, ...).
    Use `:=` for exact match; bare `:` is word/substring match.

    IMPORTANT: field names containing dots MUST be quoted, otherwise
    LogsQL treats the dot as a path separator and matches nothing. So
    `"service.name":=demo-api` works, bare `service.name:=demo-api` does not.
    Same applies in `stats by (...)` and other clauses.

    LogsQL combines filters with optional `|` pipe operations:
      - Word filter:    `error timeout`                          (free-text match)
      - Field filter:   `severity_text:=ERROR "service.name":=demo-api`
      - Time filter:    `_time:5m` or `_time:[2026-05-07T03:00:00Z, now]`
      - Pipes:          `... | stats by ("scope.name") count() as n | sort by (n) desc`

    Each returned record is a JSON object with `_msg`, `_time`, `_stream`, plus
    OTel fields (`severity_text`, `severity_number`, `service.name`, `scope.name`,
    `trace_id`, `span_id`, `code.filepath`, `code.function`, `code.lineno`, ...).

    Examples:
      query_logs('severity_text:=ERROR _time:15m')
      query_logs('"service.name":=demo-api trace_id:abc123... _time:1h')
      query_logs('_time:1h severity_text:=INFO | stats by ("scope.name") count() as n')
    """
    r = await client.post(
        f"{VLOGS_URL}/select/logsql/query",
        data={"query": query, "limit": str(limit)},
    )
    r.raise_for_status()
    out: list[dict[str, Any]] = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[:limit]


# ─── PromQL ────────────────────────────────────────────────────────────────

@mcp.tool()
async def query_metrics(
    query: str,
    range: str | None = None,
    step: str = "30s",
) -> dict[str, Any]:
    """Query VictoriaMetrics with PromQL.

    - Instant query (range=None): returns the current value(s) for the expression.
    - Range query (range='15m', '1h', ...): returns a time series over [now-range, now]
      sampled at `step`.

    Metrics are pushed via OTLP from the app SDK, so series carry OTel
    resource labels (`service.name`, `telemetry.sdk.*`) plus whatever
    attributes the app sets on the data point (e.g. `endpoint`, `operation`).

    Common patterns:
      query_metrics('app_requests_total')
      query_metrics('rate(app_requests_total[1m])', range='15m')
      query_metrics('histogram_quantile(0.95, sum by (le) (rate(app_request_duration_ms_bucket[5m])))')
      query_metrics('sum by (endpoint) (rate(app_requests_total[1m]))', range='30m', step='10s')
    """
    if range is None:
        r = await client.get(f"{VMETRICS_URL}/api/v1/query", params={"query": query})
    else:
        end = int(time.time())
        start = end - _parse_duration_seconds(range)
        r = await client.get(
            f"{VMETRICS_URL}/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "step": step},
        )
    r.raise_for_status()
    return r.json()


@mcp.tool()
async def list_metric_names(match_regex: str = ".+") -> list[str]:
    """List all metric names known to VictoriaMetrics.

    Useful as a discovery step before composing PromQL — the agent can see
    which metrics actually exist instead of guessing names.

    `match_regex` filters by name (PromQL regex syntax).
    """
    r = await client.get(
        f"{VMETRICS_URL}/api/v1/label/__name__/values",
        params={"match[]": f'{{__name__=~"{match_regex}"}}'},
    )
    r.raise_for_status()
    return r.json().get("data", [])


# ─── TraceQL / Jaeger ──────────────────────────────────────────────────────

@mcp.tool()
async def get_trace(trace_id: str) -> dict[str, Any]:
    """Fetch a single trace from VictoriaTraces by trace_id (32 hex chars).

    Returns the full Jaeger-format trace: services, processes, and all spans
    with their operationName, duration, tags (incl. http.status_code, errors),
    and parent-child relationships.

    Use this after `query_logs` returns a record with a trace_id field — that
    is the canonical cross-signal lookup pattern.
    """
    r = await client.get(f"{VTRACES_URL}/select/jaeger/api/traces/{trace_id}")
    r.raise_for_status()
    return r.json()


@mcp.tool()
async def search_traces(
    service: str | None = None,
    operation: str | None = None,
    lookback: str = "15m",
    limit: int = 10,
    min_duration_ms: int | None = None,
) -> dict[str, Any]:
    """Search traces in VictoriaTraces (Jaeger-compatible search API).

    All parameters are optional; combine to narrow results. `min_duration_ms`
    is useful for finding slow requests.

    Examples:
      search_traces(service='demo-api', lookback='30m')
      search_traces(service='demo-api', operation='GET /work', min_duration_ms=100)
      search_traces(lookback='1h', limit=20)
    """
    end_us = int(time.time() * 1_000_000)
    start_us = end_us - _parse_duration_seconds(lookback) * 1_000_000
    params: dict[str, Any] = {"start": start_us, "end": end_us, "limit": limit}
    if service:
        params["service"] = service
    if operation:
        params["operation"] = operation
    if min_duration_ms is not None:
        params["minDuration"] = f"{min_duration_ms}ms"
    r = await client.get(f"{VTRACES_URL}/select/jaeger/api/traces", params=params)
    r.raise_for_status()
    return r.json()


@mcp.tool()
async def list_services() -> list[str]:
    """List all services known to VictoriaTraces. Discovery helper for search_traces."""
    r = await client.get(f"{VTRACES_URL}/select/jaeger/api/services")
    r.raise_for_status()
    return r.json().get("data", [])


# ─── Loop closure: change → restart → workload → observe ──────────────────

@mcp.tool()
async def restart_app(rebuild: bool = False, timeout_s: int = 30) -> dict[str, Any]:
    """Restart the demo-app container, then poll /health until it responds 200.

    Use this after editing files in app/ to make changes take effect.

    - rebuild=False: just restart the existing container (~2s)
    - rebuild=True: `docker compose up --build -d app` to rebuild image (~10-30s)

    Returns timing and the final health status. Raises if /health doesn't come
    back within timeout_s seconds.
    """
    started = time.time()
    if rebuild:
        cmd = ["docker", "compose", "up", "--build", "-d", "app"]
    else:
        cmd = ["docker", "compose", "restart", "app"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=COMPOSE_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"docker compose failed: {stderr.decode()[:500]}")

    deadline = time.time() + timeout_s
    last_err = ""
    while time.time() < deadline:
        try:
            r = await client.get(f"{APP_URL}/health", timeout=2.0)
            if r.status_code == 200:
                return {
                    "ok": True,
                    "rebuilt": rebuild,
                    "restart_seconds": round(time.time() - started, 2),
                    "ready_at": time.time(),
                }
        except Exception as e:
            last_err = str(e)
        await asyncio.sleep(0.5)
    raise RuntimeError(f"app /health did not return 200 within {timeout_s}s; last error: {last_err}")


@mcp.tool()
async def run_workload(
    profile: Literal["mixed", "normal", "errors", "load"] = "mixed",
    requests: int = 20,
    concurrency: int = 4,
) -> dict[str, Any]:
    """Generate a known traffic pattern against the demo app and return the
    [start, end] window. Pair with `query_logs` / `query_metrics` / `search_traces`
    using `_time:` filters bounded by that window to verify the effect of a change.

    Profiles:
      - normal: all GET /work
      - errors: all GET /boom (each returns 500, generates ERROR log + exception span)
      - mixed:  ~80% /work, ~20% /boom
      - load:   `requests` parallel GET /work, no errors

    The OTel SDK exports metrics every 5s (PeriodicExportingMetricReader)
    — wait ~6s after this returns before querying VictoriaMetrics for fresh
    counter values.
    """
    import random

    started_at = time.time()

    if profile == "normal":
        plan = ["/work"] * requests
    elif profile == "errors":
        plan = ["/boom"] * requests
    elif profile == "mixed":
        plan = [random.choices(["/work", "/boom"], weights=[80, 20])[0] for _ in range(requests)]
    elif profile == "load":
        plan = ["/work"] * requests
    else:
        raise ValueError(f"unknown profile {profile!r}")

    sem = asyncio.Semaphore(concurrency)
    counts = {"2xx": 0, "5xx": 0, "other": 0}

    async def hit(path: str) -> None:
        async with sem:
            try:
                r = await client.get(f"{APP_URL}{path}", timeout=10.0)
                if 200 <= r.status_code < 300:
                    counts["2xx"] += 1
                elif 500 <= r.status_code < 600:
                    counts["5xx"] += 1
                else:
                    counts["other"] += 1
            except Exception:
                counts["other"] += 1

    await asyncio.gather(*(hit(p) for p in plan))
    ended_at = time.time()

    return {
        "profile": profile,
        "requests_planned": requests,
        "responses": counts,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": round(ended_at - started_at, 2),
        "logs_query_hint": f'_time:[{int(started_at)}, {int(ended_at) + 1}]',
        "metrics_note": "OTLP export interval is 5s — sleep ~6s before querying metrics for the latest values",
    }


if __name__ == "__main__":
    mcp.run()
