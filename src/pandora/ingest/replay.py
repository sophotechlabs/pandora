from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from pandora.events.store import EventStore
from pandora.ingest.models import EnvelopeState, RawEnvelope
from pandora.ingest.processor import process_envelope

DEFAULT_LIMIT = 500
STATE_SETS = {
    "failed": (EnvelopeState.FAILED,),
    "pending": (EnvelopeState.PENDING,),
    "all": (EnvelopeState.FAILED, EnvelopeState.PENDING),
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplayResult:
    attempted: int = 0
    done: int = 0
    failed: int = 0


def pending_envelopes(
    states: Sequence[str], limit: int, project_slug: str = ""
) -> list[RawEnvelope]:
    query = RawEnvelope.objects.filter(state__in=states).order_by("pk")
    if project_slug:
        query = query.filter(project__slug=project_slug)
    return list(query[:limit])


def replay(
    states: Sequence[str],
    limit: int,
    project_slug: str = "",
    store: EventStore | None = None,
) -> ReplayResult:
    envelopes = pending_envelopes(states, limit, project_slug)
    done = 0
    failed = 0
    for envelope in envelopes:
        _reclaim(envelope)
        process_envelope(envelope.pk, store=store)
        envelope.refresh_from_db()
        if envelope.state == EnvelopeState.DONE:
            done += 1
            continue
        failed += 1
        logger.warning(
            "envelope %s still not applied after replay: %s",
            envelope.pk,
            envelope.error,
        )
    return ReplayResult(attempted=len(envelopes), done=done, failed=failed)


def _reclaim(envelope: RawEnvelope) -> None:
    if envelope.state == EnvelopeState.PENDING:
        return
    envelope.state = EnvelopeState.PENDING
    envelope.error = ""
    envelope.save(update_fields=["state", "error"])
