import logging
import os

import httpx
from fastapi import FastAPI, HTTPException
from opentelemetry import metrics, trace

import obs

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "test-app")
DEMO_API_URL = os.getenv("DEMO_API_URL", "http://app:8000")

app = FastAPI()
obs.init(app, service_name=SERVICE_NAME)

tracer = trace.get_tracer(SERVICE_NAME)
meter = metrics.get_meter(SERVICE_NAME)
chain_counter = meter.create_counter("test_app_chain_calls_total")
logger = logging.getLogger("test-app")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/chain")
async def chain():
    """Calls demo-api/work to exercise cross-service trace propagation.
    httpx auto-instrumentation injects W3C traceparent headers so demo-api
    sees this request as a child of our span — same trace_id, two services."""
    chain_counter.add(1)
    with tracer.start_as_current_span("call_demo_api") as span:
        span.set_attribute("downstream", "demo-api")
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{DEMO_API_URL}/work")
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail="downstream failed")
            data = resp.json()
    logger.info("chain complete downstream_delay_ms=%s", data.get("delay_ms"))
    return {"ok": True, "downstream": data}
