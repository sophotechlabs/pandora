from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from prometheus_client import Counter

from pandora.notify.models import Delivery, DeliveryState, Destination
from pandora.notify.senders import SendError, send

BATCH = 200
MAX_ATTEMPTS = 5
CLAIM_TTL = timedelta(minutes=5)
BACKOFF = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(hours=1),
)

DELIVERIES = Counter(
    "pandora_notify_deliveries_total",
    "Notification deliveries the worker finished, by destination kind and state",
    ["kind", "state"],
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliverReport:
    sent: int = 0
    failed: int = 0
    retried: int = 0


def _ready(now: datetime) -> QuerySet[Delivery]:
    ready = Q(send_after=None) | Q(send_after__lte=now)
    pending = Q(state=DeliveryState.PENDING) & ready
    abandoned = Q(state=DeliveryState.SENDING, send_after__lte=now)
    return Delivery.objects.filter(pending | abandoned)


def due(now: datetime, limit: int = BATCH) -> list[Delivery]:
    return list(
        _ready(now)
        .select_related("destination", "issue")
        .order_by("created_at", "id")[:limit]
    )


def _grouped(rows: list[Delivery]) -> dict[int, list[Delivery]]:
    grouped: dict[int, list[Delivery]] = defaultdict(list)
    for row in rows:
        grouped[row.destination_id].append(row)
    return grouped


def _held(destination: Destination, rows: list[Delivery], now: datetime) -> bool:
    if not destination.digest_seconds:
        return False
    oldest = min(row.created_at for row in rows)
    return now - oldest < timedelta(seconds=destination.digest_seconds)


def _backoff(attempts: int) -> timedelta:
    index = min(attempts - 1, len(BACKOFF) - 1)
    return BACKOFF[index]


def run_once(now: datetime | None = None, limit: int = BATCH) -> DeliverReport:
    moment = now or timezone.now()
    sent = 0
    failed = 0
    retried = 0
    remaining = limit
    destination_ids = list(_grouped(due(moment, limit)))
    for destination_id in destination_ids:
        rows = _claim(destination_id, moment, remaining)
        if not rows:
            continue
        remaining -= len(rows)
        destination = rows[0].destination
        try:
            send(destination, rows)
        except SendError as error:
            failed_now, retried_now = _record_failure(rows, destination, error, moment)
            failed += failed_now
            retried += retried_now
            continue
        _record_success(rows, destination, moment)
        sent += len(rows)
        if remaining < 1:
            break
    return DeliverReport(sent=sent, failed=failed, retried=retried)


def _claim(destination_id: int, now: datetime, limit: int) -> list[Delivery]:
    with transaction.atomic():
        try:
            destination = Destination.objects.select_for_update().get(pk=destination_id)
        except Destination.DoesNotExist:
            return []
        rows = list(
            _ready(now)
            .filter(destination=destination)
            .select_related("destination", "issue")
            .order_by("created_at", "id")[:limit]
        )
        if not rows or _held(destination, rows, now):
            return []
        lease = now + CLAIM_TTL
        Delivery.objects.filter(pk__in=[row.pk for row in rows]).update(
            state=DeliveryState.SENDING,
            send_after=lease,
        )
        for row in rows:
            row.state = DeliveryState.SENDING
            row.send_after = lease
        return rows


def _record_success(
    rows: list[Delivery], destination: Destination, now: datetime
) -> None:
    Delivery.objects.filter(pk__in=[row.pk for row in rows]).update(
        state=DeliveryState.SENT, sent_at=now, send_after=None, error=""
    )
    DELIVERIES.labels(kind=destination.kind, state=DeliveryState.SENT).inc(len(rows))


def _record_failure(
    rows: list[Delivery], destination: Destination, error: SendError, now: datetime
) -> tuple[int, int]:
    failed = 0
    retried = 0
    for row in rows:
        attempts = row.attempts + 1
        if attempts >= MAX_ATTEMPTS:
            row.state = DeliveryState.FAILED
            row.send_after = None
            failed += 1
        else:
            row.state = DeliveryState.PENDING
            row.send_after = now + _backoff(attempts)
            retried += 1
        row.attempts = attempts
        row.error = str(error)[:2000]
        row.save(update_fields=["state", "attempts", "error", "send_after"])
    if failed:
        DELIVERIES.labels(kind=destination.kind, state=DeliveryState.FAILED).inc(failed)
    log.warning(
        "notify: %s deliveries to %s failed: %s", len(rows), destination.name, error
    )
    return failed, retried


def prune(before: datetime) -> int:
    deleted, _ = Delivery.objects.filter(
        state=DeliveryState.SENT, sent_at__lt=before
    ).delete()
    return deleted
