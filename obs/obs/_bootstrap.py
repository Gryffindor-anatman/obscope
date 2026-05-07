import logging
import os
from typing import Optional

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_ON,
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
)

_initialized = False


class _DropAsgiSendSampler(Sampler):
    """Drop ASGI 'http send' / 'http receive' sub-spans created by the
    fastapi/asgi instrumentor. They double the span count per request
    without adding diagnostic value."""

    _NOISY_SUFFIXES = (" http send", " http receive")

    def __init__(self, parent: Sampler) -> None:
        self._parent = parent

    def should_sample(
        self,
        parent_context: Optional[Context],
        trace_id: int,
        name: str,
        kind=None,
        attributes=None,
        links=None,
        trace_state=None,
    ) -> SamplingResult:
        if name.endswith(self._NOISY_SUFFIXES):
            return SamplingResult(Decision.DROP)
        return self._parent.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return f"DropAsgiSend({self._parent.get_description()})"


def init(
    app=None,
    *,
    service_name: Optional[str] = None,
    excluded_urls: str = "/health",
    log_level: int = logging.INFO,
    metric_export_interval_ms: int = 5000,
) -> None:
    """One-shot observability bootstrap.

    Reads OTLP endpoints from standard OTel env vars
    (OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_EXPORTER_OTLP_{TRACES,LOGS,METRICS}_ENDPOINT).
    Sets global TracerProvider / LoggerProvider / MeterProvider and
    instruments FastAPI + httpx. Metrics push via OTLP every
    `metric_export_interval_ms`.
    """
    global _initialized
    if _initialized:
        return

    service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "unknown-service")
    resource = Resource.create({"service.name": service_name})

    tp = TracerProvider(
        resource=resource,
        sampler=_DropAsgiSendSampler(ParentBased(ALWAYS_ON)),
    )
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tp)

    lp = LoggerProvider(resource=resource)
    lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(lp)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger().addHandler(LoggingHandler(level=log_level, logger_provider=lp))
    # Silence libraries used by the OTLP exporters themselves to avoid
    # log → exporter → log feedback loops.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("opentelemetry").setLevel(logging.WARNING)

    metrics.set_meter_provider(
        MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(),
                    export_interval_millis=metric_export_interval_ms,
                )
            ],
        )
    )

    HTTPXClientInstrumentor().instrument()
    if app is not None:
        FastAPIInstrumentor.instrument_app(
            app, tracer_provider=tp, excluded_urls=excluded_urls
        )

    _initialized = True
