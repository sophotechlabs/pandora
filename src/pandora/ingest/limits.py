from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F

from pandora.ingest.models import IngestCounter, IngestQuota

HOUR_SECONDS = 3600
SPIKE_HISTORY = 24


def bucket_start(moment: datetime, window_seconds: int) -> datetime:
    window = max(window_seconds, 1)
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % window), tz=moment.tzinfo)


def seconds_left(moment: datetime, window_seconds: int) -> int:
    window = max(window_seconds, 1)
    elapsed = int(moment.timestamp()) - int(bucket_start(moment, window).timestamp())
    return max(1, window - elapsed)


def quota_for(project_id: int) -> IngestQuota | None:
    quotas = IngestQuota.objects.filter(active=True)
    scoped = quotas.filter(project_id=project_id).order_by("limit").first()
    if scoped is not None:
        return scoped
    return quotas.filter(project=None).order_by("limit").first()


def counter_key(project_id: int, quota: IngestQuota) -> str:
    if quota.project_id is not None:
        return f"project:{project_id}"
    return "global"


def spike_key(project_id: int) -> str:
    return f"spike:{project_id}"


def hit(key: str, moment: datetime, window_seconds: int) -> int:
    bucket = bucket_start(moment, window_seconds)
    with transaction.atomic():
        counter, created = IngestCounter.objects.get_or_create(
            key=key, bucket=bucket, defaults={"count": 1}
        )
        if created:
            return 1
        IngestCounter.objects.filter(pk=counter.pk).update(count=F("count") + 1)
        counter.refresh_from_db(fields=["count"])
    return counter.count


def baseline(key: str, moment: datetime) -> float:
    current = bucket_start(moment, HOUR_SECONDS)
    window = timedelta(seconds=HOUR_SECONDS * SPIKE_HISTORY)
    counts = list(
        IngestCounter.objects.filter(
            key=key, bucket__lt=current, bucket__gte=current - window
        ).values_list("count", flat=True)
    )
    if not counts:
        return 0.0
    return float(statistics.median(counts))


def spiking(key: str, count: int, moment: datetime) -> bool:
    if count < settings.PANDORA_SPIKE_FLOOR:
        return False
    normal = baseline(key, moment)
    if normal <= 0:
        return False
    return count > normal * settings.PANDORA_SPIKE_FACTOR


def prune(before: datetime) -> int:
    deleted, _ = IngestCounter.objects.filter(bucket__lt=before).delete()
    return deleted
