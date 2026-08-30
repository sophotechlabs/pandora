from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import connection, transaction

from pandora.events.store import EventStore
from pandora.ingest.models import EnvelopeState, RawEnvelope
from pandora.ingest.processor import process_envelope

BATCH = 50
log = logging.getLogger(__name__)


@dataclass
class Pass:
    claimed: int = 0
    done: int = 0
    failed: int = 0

    def line(self) -> str:
        return (
            f"consume: {self.claimed} claimed, {self.done} applied,"
            f" {self.failed} still failing"
        )


def claim(batch: int = BATCH) -> list[int]:
    """Take a batch of pending envelopes without two consumers taking the same one.

    `SELECT ... FOR UPDATE SKIP LOCKED` on Postgres; on SQLite the single writer
    makes the plain update the same guarantee. The envelope table has been a
    durable queue since the first commit — this is the consumer it never had.
    """
    with transaction.atomic():
        rows = (
            RawEnvelope.objects.select_for_update(skip_locked=_skips_locked())
            .filter(state=EnvelopeState.PENDING)
            .order_by("received_at")
            .values_list("pk", flat=True)[:batch]
        )
        claimed = list(rows)
        if claimed:
            RawEnvelope.objects.filter(pk__in=claimed).update(
                state=EnvelopeState.CLAIMED
            )
    return claimed


def run_once(store: EventStore | None = None, batch: int = BATCH) -> Pass:
    report = Pass()
    for envelope_id in claim(batch):
        report.claimed += 1
        _apply(report, envelope_id, store)
    return report


def release(envelope_ids: list[int]) -> int:
    return RawEnvelope.objects.filter(
        pk__in=envelope_ids, state=EnvelopeState.CLAIMED
    ).update(state=EnvelopeState.PENDING)


def reclaim_stale(before: datetime) -> int:
    """Put back what a consumer claimed and never finished.

    A killed process leaves its batch claimed; without this they would sit there
    forever, which is exactly the failure the durable table exists to avoid.
    """
    return RawEnvelope.objects.filter(
        state=EnvelopeState.CLAIMED, received_at__lt=before
    ).update(state=EnvelopeState.PENDING)


def _apply(report: Pass, envelope_id: int, store: EventStore | None) -> None:
    RawEnvelope.objects.filter(pk=envelope_id).update(state=EnvelopeState.PENDING)
    process_envelope(envelope_id, store=store)
    state = (
        RawEnvelope.objects.filter(pk=envelope_id)
        .values_list("state", flat=True)
        .first()
    )
    if state == EnvelopeState.DONE:
        report.done += 1
        return
    report.failed += 1


def _skips_locked() -> bool:
    return connection.features.has_select_for_update_skip_locked
