from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Protocol

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string
from prometheus_client import Counter

from pandora.ingest import limits

SPIKE_WINDOW = limits.HOUR_SECONDS

GATE_CHECKS = Counter(
    "pandora_ingest_gate_checks_total",
    "Ingest gate checks performed before the durable write",
)
GATE_REJECTIONS = Counter(
    "pandora_ingest_gate_rejections_total",
    "Ingest gate rejections before the durable write",
    ["reason"],
)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    status: int = HTTPStatus.OK
    reason: str = ""
    retry_after: int = 0
    categories: str = "error"

    def headers(self) -> dict[str, str]:
        if self.allowed or not self.retry_after:
            return {}
        return {
            "X-Sentry-Rate-Limits": (
                f"{self.retry_after}:{self.categories}:key:{self.reason}"
            ),
            "Retry-After": str(self.retry_after),
        }


class Gate(Protocol):
    def check(self, project_id: int, content_length: int) -> Verdict: ...


class PassThroughGate:
    def __init__(self, max_bytes: int | None = None) -> None:
        if max_bytes is None:
            max_bytes = settings.PANDORA_INGEST_MAX_BYTES
        self.max_bytes = max_bytes

    def check(self, project_id: int, content_length: int) -> Verdict:
        GATE_CHECKS.inc()
        if content_length > self.max_bytes:
            GATE_REJECTIONS.labels(reason="oversized").inc()
            return Verdict(
                allowed=False,
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                reason="oversized",
            )
        return Verdict(allowed=True)


class RateLimitGate(PassThroughGate):
    def check(self, project_id: int, content_length: int) -> Verdict:
        verdict = super().check(project_id, content_length)
        if not verdict.allowed:
            return verdict
        now = timezone.now()
        refused = self._quota(project_id, now)
        if refused is not None:
            return refused
        return self._spike(project_id, now)

    def _quota(self, project_id: int, now: datetime) -> Verdict | None:
        quota = limits.quota_for(project_id)
        if quota is None:
            return None
        key = limits.counter_key(project_id, quota)
        count = limits.hit(key, now, quota.window_seconds)
        if count <= quota.limit:
            return None
        GATE_REJECTIONS.labels(reason="rate_limited").inc()
        return Verdict(
            allowed=False,
            status=HTTPStatus.TOO_MANY_REQUESTS,
            reason="rate_limited",
            retry_after=limits.seconds_left(now, quota.window_seconds),
        )

    def _spike(self, project_id: int, now: datetime) -> Verdict:
        if not settings.PANDORA_SPIKE_ENABLED:
            return Verdict(allowed=True)
        key = limits.spike_key(project_id)
        count = limits.hit(key, now, SPIKE_WINDOW)
        if not limits.spiking(key, count, now):
            return Verdict(allowed=True)
        GATE_REJECTIONS.labels(reason="spike").inc()
        return Verdict(
            allowed=False,
            status=HTTPStatus.TOO_MANY_REQUESTS,
            reason="spike_protection",
            retry_after=limits.seconds_left(now, SPIKE_WINDOW),
        )


def get_gate() -> Gate:
    gate_class = import_string(settings.PANDORA_GATE)
    return gate_class()
