"""Prometheus metric definitions."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# Latency buckets cover the full range from a cached static asset to
# a full sync. Anything slower than 10 s lands in the implicit +Inf bucket.
_LATENCY_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


HTTP_REQUESTS = Counter(
    "ankipaper_http_requests_total",
    "Total HTTP requests processed.",
    labelnames=("method", "route", "status_class"),
)

HTTP_REQUEST_DURATION = Histogram(
    "ankipaper_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    labelnames=("route",),
    buckets=_LATENCY_BUCKETS,
)
