from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from pandora.core import database
from pandora.events.store import get_store
from pandora.ingest import limits
from pandora.ingest.models import EnvelopeState, ProcessedEvent, RawEnvelope
from pandora.issues.models import HourlyStat, IssueActivity, SilenceLink
from pandora.notify import deliver as notify_deliver

logger = logging.getLogger(__name__)

MONTHS_AHEAD = 2
COUNTER_RETENTION = timedelta(days=2)


@dataclass(frozen=True)
class PruneResult:
    events: int
    envelopes: int
    processed_events: int
    silences: int
    hourly_stats: int = 0
    activities: int = 0
    counters: int = 0
    deliveries: int = 0


def prune_expired(now: datetime) -> PruneResult:
    retention_cutoff = now - timedelta(days=settings.PANDORA_RETENTION_DAYS)
    envelope_cutoff = now - timedelta(days=settings.PANDORA_ENVELOPE_RETENTION_DAYS)

    store = get_store()
    events = store.prune(retention_cutoff)
    envelopes, _ = RawEnvelope.objects.filter(
        state=EnvelopeState.DONE,
        received_at__lt=envelope_cutoff,
    ).delete()
    processed_events, _ = ProcessedEvent.objects.filter(
        seen_at__lt=retention_cutoff,
    ).delete()
    silences, _ = SilenceLink.objects.filter(expires_at__lt=now).delete()
    hourly_stats, _ = HourlyStat.objects.filter(hour__lt=retention_cutoff).delete()
    activities, _ = IssueActivity.objects.filter(at__lt=retention_cutoff).delete()
    counters = limits.prune(now - COUNTER_RETENTION)
    deliveries = notify_deliver.prune(retention_cutoff)
    store.ensure_partitions(months_ahead=MONTHS_AHEAD)
    database.incremental_vacuum()
    database.refresh_size()

    result = PruneResult(
        events=events,
        envelopes=envelopes,
        processed_events=processed_events,
        silences=silences,
        hourly_stats=hourly_stats,
        activities=activities,
        counters=counters,
        deliveries=deliveries,
    )
    logger.info(
        "prune: %s events, %s envelopes, %s processed events, %s silences,"
        " %s hourly stats, %s activities, %s ingest counters, %s deliveries",
        result.events,
        result.envelopes,
        result.processed_events,
        result.silences,
        result.hourly_stats,
        result.activities,
        result.counters,
        result.deliveries,
    )
    return result


class Command(BaseCommand):
    help = "Delete data past its retention window and keep event partitions ahead"

    def handle(self, *args: Any, **options: Any) -> None:
        result = prune_expired(timezone.now())
        self.stdout.write(
            f"prune: {result.events} events, {result.envelopes} envelopes, "
            f"{result.processed_events} processed events, {result.silences} silences, "
            f"{result.hourly_stats} hourly stats, {result.activities} activities, "
            f"{result.counters} ingest counters, {result.deliveries} deliveries"
        )
