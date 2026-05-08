from opentelemetry import metrics

from config import settings

meter = metrics.get_meter(settings.SERVICE_NAME)

request_counter = meter.create_counter("app_requests_total")
request_duration = meter.create_histogram("app_request_duration_ms")
redis_ops_total = meter.create_counter("redis_ops_total")
httpbin_requests_total = meter.create_counter("httpbin_requests_total")
