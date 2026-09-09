from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from pandora.core.models import Project
from pandora.perf import service

HOURS = 24


@dataclass(frozen=True)
class Endpoint:
    name: str
    per_hour: int
    fast_ms: int
    slow_ms: int
    slow_every: int
    fail_every: int
    db_share: float


ENDPOINTS = (
    Endpoint(
        name="GET /api/orders",
        per_hour=3,
        fast_ms=45,
        slow_ms=1800,
        slow_every=3,
        fail_every=0,
        db_share=0.7,
    ),
    Endpoint(
        name="POST /api/checkout",
        per_hour=2,
        fast_ms=220,
        slow_ms=4200,
        slow_every=2,
        fail_every=2,
        db_share=0.35,
    ),
    Endpoint(
        name="GET /api/items",
        per_hour=6,
        fast_ms=18,
        slow_ms=60,
        slow_every=5,
        fail_every=0,
        db_share=0.5,
    ),
)


def seed(project: Project, environment: str, now: datetime) -> int:
    """Push real transaction payloads through the real aggregation.

    The demo has to exercise the same path production does, so the numbers on
    the performance page are the ones the histogram actually produces rather
    than rows written straight into the table.
    """
    seeded = 0
    for endpoint in ENDPOINTS:
        for hour in range(HOURS):
            started = now - timedelta(hours=hour)
            for index in range(endpoint.per_hour):
                service.record(
                    project,
                    _payload(endpoint, started, index),
                    started,
                    environment=environment,
                )
                seeded += 1
    return seeded


def _payload(endpoint: Endpoint, started: datetime, index: int) -> dict:
    duration = endpoint.fast_ms
    if endpoint.slow_every and index % endpoint.slow_every == 0:
        duration = endpoint.slow_ms
    status = "ok"
    if endpoint.fail_every and index % endpoint.fail_every == 0:
        status = "internal_error"
    moment = started + timedelta(seconds=index)
    inside = duration * endpoint.db_share
    return {
        "type": "transaction",
        "transaction": endpoint.name,
        "start_timestamp": moment.timestamp(),
        "timestamp": moment.timestamp() + duration / 1000,
        "contexts": {"trace": {"op": "http.server", "status": status}},
        "spans": [
            {
                "op": "db",
                "description": "SELECT ...",
                "start_timestamp": moment.timestamp(),
                "timestamp": moment.timestamp() + inside / 1000,
            },
            {
                "op": "http.client",
                "description": "GET https://payments.example.test/charge",
                "start_timestamp": moment.timestamp() + inside / 1000,
                "timestamp": moment.timestamp() + duration / 2000 + inside / 1000,
            },
        ],
    }
