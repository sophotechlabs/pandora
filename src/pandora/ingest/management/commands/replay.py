from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.core.management.base import BaseCommand

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


class Command(BaseCommand):
    help = "Re-run the consumer over envelopes that never reached done"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--state",
            choices=sorted(STATE_SETS),
            default="all",
            help="which envelopes to pick up (default: all)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_LIMIT,
            help=f"how many to attempt in one run (default: {DEFAULT_LIMIT})",
        )
        parser.add_argument(
            "--project",
            default="",
            help="restrict to one project slug",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="report what would be replayed and change nothing",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        states = STATE_SETS[options["state"]]
        limit = options["limit"]
        project_slug = options["project"].strip()

        if options["dry_run"]:
            waiting = pending_envelopes(states, limit, project_slug)
            self.stdout.write(f"replay: {len(waiting)} envelope(s) would be replayed")
            for envelope in waiting:
                self.stdout.write(f"  {envelope.pk} {envelope.source} {envelope.state}")
            return

        result = replay(states, limit, project_slug)
        self.stdout.write(
            f"replay: {result.attempted} attempted, "
            f"{result.done} done, {result.failed} still failing"
        )
