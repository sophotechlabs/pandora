from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Protocol

from django.conf import settings
from django.utils.module_loading import import_string
from prometheus_client import Counter

from pandora.core.models import IngestToken

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


class Gate(Protocol):
    def check(self, token: IngestToken, content_length: int) -> Verdict: ...


class PassThroughGate:
    def __init__(self, max_bytes: int | None = None) -> None:
        if max_bytes is None:
            max_bytes = settings.PANDORA_INGEST_MAX_BYTES
        self.max_bytes = max_bytes

    def check(self, token: IngestToken, content_length: int) -> Verdict:
        GATE_CHECKS.inc()
        if content_length > self.max_bytes:
            GATE_REJECTIONS.labels(reason="oversized").inc()
            return Verdict(
                allowed=False,
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                reason="oversized",
            )
        return Verdict(allowed=True)


def get_gate() -> Gate:
    gate_class = import_string(settings.PANDORA_GATE)
    return gate_class()
