import logging
import random
import time

from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def do_work() -> float:
    with tracer.start_as_current_span("do_work") as span:
        delay = random.uniform(0.02, 0.15)
        span.set_attribute("simulated_delay_s", delay)
        time.sleep(delay)
        logger.info("did some work delay=%.3fs", delay)
    return delay


def slow_dependency(budget_ms: int) -> tuple[bool, float]:
    start = time.perf_counter()
    with tracer.start_as_current_span("slow_dependency") as span:
        delay = random.uniform(0.02, 0.25)
        span.set_attribute("simulated_delay_s", delay)
        span.set_attribute("budget_ms", budget_ms)
        time.sleep(delay)
    elapsed_ms = (time.perf_counter() - start) * 1000
    within = elapsed_ms <= budget_ms
    if not within:
        logger.error(
            "request timed out elapsed_ms=%.2f budget_ms=%d", elapsed_ms, budget_ms
        )
    return within, elapsed_ms
