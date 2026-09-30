from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.db import transaction as db_transaction
from django.db.models import F, Sum

from pandora.core.models import Project
from pandora.perf.models import (
    BOUNDARIES_MS,
    OP_MAX,
    TRANSACTION_MAX,
    SpanSummary,
    TransactionBucket,
    empty_histogram,
)

MAX_SPANS = 200
MAX_DURATION_MS = 1000 * 60 * 60
OK_STATUSES = frozenset({"ok", ""})
DEFAULT_OP = "other"
SECOND_MS = 1000.0


class TransactionError(ValueError):
    pass


@dataclass(frozen=True)
class Reading:
    transaction: str
    count: int
    failures: int
    duration_sum: float
    duration_max: float
    histogram: list[int]

    @property
    def failure_rate(self) -> float:
        if not self.count:
            return 0.0
        return self.failures / self.count * 100

    @property
    def average(self) -> float:
        if not self.count:
            return 0.0
        return self.duration_sum / self.count

    def quantile(self, fraction: float) -> float:
        found = quantile(self.histogram, fraction)
        if self.duration_max <= 0:
            return found
        return min(found, self.duration_max)

    @property
    def p50(self) -> float:
        return self.quantile(0.5)

    @property
    def p95(self) -> float:
        return self.quantile(0.95)

    @property
    def p99(self) -> float:
        return self.quantile(0.99)


def bucket_index(duration_ms: float) -> int:
    return bisect.bisect_left(BOUNDARIES_MS, duration_ms)


def quantile(histogram: Sequence[int], fraction: float) -> float:
    """Estimate a percentile from the fixed histogram.

    The answer is the upper edge of the bucket the rank falls in — honest to
    the resolution actually stored, rather than an interpolation that would
    imply a precision the buckets do not have.
    """
    total = sum(histogram)
    if total <= 0:
        return 0.0
    wanted = max(1, min(total, round(total * fraction)))
    seen = 0
    for index, count in enumerate(histogram):
        seen += count
        if seen < wanted:
            continue
        if index >= len(BOUNDARIES_MS):
            return BOUNDARIES_MS[-1]
        return BOUNDARIES_MS[index]
    return BOUNDARIES_MS[-1]


def is_transaction(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    return str(payload.get("type", "")) == "transaction"


def record(
    project: Project,
    payload: Any,
    received_at: datetime,
    environment: str = "",
) -> TransactionBucket | None:
    """Fold one transaction into the hour it belongs to.

    Nothing about the individual request survives this call, which is the whole
    design: the storage cost is the number of endpoints, not the number of
    requests. The row is locked for the read-modify-write, because a histogram
    cannot be incremented with an expression the way a counter can.
    """
    if not isinstance(payload, Mapping):
        raise TransactionError("transaction is not a JSON object")
    name = _text(payload.get("transaction"), TRANSACTION_MAX)
    if not name:
        return None
    duration = _duration(payload)
    if duration is None:
        return None
    hour = _hour(_started(payload, received_at))
    failed = _failed(payload)

    with db_transaction.atomic():
        bucket = _bucket(project, name, payload, environment, hour)
        locked = TransactionBucket.objects.select_for_update().get(pk=bucket.pk)
        histogram = list(locked.histogram or empty_histogram())
        if len(histogram) != len(BOUNDARIES_MS) + 1:
            histogram = empty_histogram()
        histogram[bucket_index(duration)] += 1
        TransactionBucket.objects.filter(pk=locked.pk).update(
            count=F("count") + 1,
            failures=F("failures") + int(failed),
            duration_sum=F("duration_sum") + duration,
            duration_max=max(locked.duration_max, duration),
            histogram=histogram,
        )
        _record_spans(locked, payload)
    bucket.refresh_from_db()
    return bucket


def _bucket(
    project: Project,
    name: str,
    payload: Mapping[str, Any],
    environment: str,
    hour: datetime,
) -> TransactionBucket:
    bucket, _ = TransactionBucket.objects.get_or_create(
        project=project,
        transaction=name,
        environment=_environment(payload, environment),
        release=_text(payload.get("release"), 250),
        hour=hour,
    )
    return bucket


def _record_spans(bucket: TransactionBucket, payload: Mapping[str, Any]) -> None:
    totals: dict[str, tuple[int, float]] = {}
    spans = payload.get("spans")
    if not isinstance(spans, list):
        return
    for entry in spans[:MAX_SPANS]:
        if not isinstance(entry, Mapping):
            continue
        duration = _span_duration(entry)
        if duration is None:
            continue
        op = _text(entry.get("op"), OP_MAX) or DEFAULT_OP
        count, total = totals.get(op, (0, 0.0))
        totals[op] = (count + 1, total + duration)
    for op, (count, total) in totals.items():
        summary, created = SpanSummary.objects.get_or_create(
            bucket=bucket,
            op=op,
            defaults={"count": count, "duration_sum": total},
        )
        if created:
            continue
        SpanSummary.objects.filter(pk=summary.pk).update(
            count=F("count") + count,
            duration_sum=F("duration_sum") + total,
        )


def _duration(payload: Mapping[str, Any]) -> float | None:
    started = _timestamp(payload.get("start_timestamp"))
    ended = _timestamp(payload.get("timestamp"))
    if started is None or ended is None:
        return None
    milliseconds = (ended - started).total_seconds() * SECOND_MS
    if milliseconds < 0:
        return None
    return min(milliseconds, MAX_DURATION_MS)


def _span_duration(entry: Mapping[str, Any]) -> float | None:
    started = _timestamp(entry.get("start_timestamp"))
    ended = _timestamp(entry.get("timestamp"))
    if started is None or ended is None:
        return None
    milliseconds = (ended - started).total_seconds() * SECOND_MS
    if milliseconds < 0:
        return None
    return min(milliseconds, MAX_DURATION_MS)


def _started(payload: Mapping[str, Any], received_at: datetime) -> datetime:
    started = _timestamp(payload.get("start_timestamp"))
    if started is None:
        return received_at
    return started


def _failed(payload: Mapping[str, Any]) -> bool:
    contexts = payload.get("contexts")
    if not isinstance(contexts, Mapping):
        return False
    trace = contexts.get("trace")
    if not isinstance(trace, Mapping):
        return False
    status = str(trace.get("status", "")).strip().lower()
    return status not in OK_STATUSES


def _environment(payload: Mapping[str, Any], fallback: str) -> str:
    declared = _text(payload.get("environment"), 100)
    if declared:
        return declared
    return fallback[:100]


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _timestamp(raw: Any) -> datetime | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC)
        except (ValueError, OverflowError, OSError):
            return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def readings(
    project: Project,
    starts_at: datetime,
    ends_at: datetime,
    environment: str = "",
    pattern: str = "",
) -> list[Reading]:
    """One row per endpoint over the window, folded from the hourly buckets."""
    buckets = TransactionBucket.objects.filter(
        project=project, hour__gte=_hour(starts_at), hour__lt=ends_at
    )
    if environment:
        buckets = buckets.filter(environment=environment)
    if pattern:
        buckets = _matching(buckets, pattern)
    folded: dict[str, Reading] = {}
    for bucket in buckets:
        folded[bucket.transaction] = _fold(folded.get(bucket.transaction), bucket)
    return sorted(folded.values(), key=lambda row: (-row.count, row.transaction))


def _matching(buckets: Any, pattern: str) -> Any:
    if "*" not in pattern:
        return buckets.filter(transaction=pattern)
    head, _, tail = pattern.partition("*")
    if head and tail:
        return buckets.filter(transaction__startswith=head, transaction__endswith=tail)
    if head:
        return buckets.filter(transaction__startswith=head)
    if tail:
        return buckets.filter(transaction__endswith=tail)
    return buckets


def _fold(current: Reading | None, bucket: TransactionBucket) -> Reading:
    histogram = list(bucket.histogram or empty_histogram())
    if len(histogram) != len(BOUNDARIES_MS) + 1:
        histogram = empty_histogram()
    if current is None:
        return Reading(
            transaction=bucket.transaction,
            count=bucket.count,
            failures=bucket.failures,
            duration_sum=bucket.duration_sum,
            duration_max=bucket.duration_max,
            histogram=histogram,
        )
    return Reading(
        transaction=current.transaction,
        count=current.count + bucket.count,
        failures=current.failures + bucket.failures,
        duration_sum=current.duration_sum + bucket.duration_sum,
        duration_max=max(current.duration_max, bucket.duration_max),
        histogram=[
            left + right
            for left, right in zip(current.histogram, histogram, strict=True)
        ],
    )


def combined(
    project: Project,
    starts_at: datetime,
    ends_at: datetime,
    environment: str = "",
    pattern: str = "",
) -> Reading | None:
    """Every matching endpoint folded into one reading, for a threshold."""
    rows = readings(project, starts_at, ends_at, environment, pattern)
    if not rows:
        return None
    total = Reading(
        transaction=pattern or "*",
        count=0,
        failures=0,
        duration_sum=0.0,
        duration_max=0.0,
        histogram=empty_histogram(),
    )
    for row in rows:
        total = Reading(
            transaction=total.transaction,
            count=total.count + row.count,
            failures=total.failures + row.failures,
            duration_sum=total.duration_sum + row.duration_sum,
            duration_max=max(total.duration_max, row.duration_max),
            histogram=[
                left + right
                for left, right in zip(total.histogram, row.histogram, strict=True)
            ],
        )
    return total


def span_breakdown(
    project: Project, transaction_name: str, starts_at: datetime, ends_at: datetime
) -> list[tuple[str, int, float]]:
    rows = (
        SpanSummary.objects.filter(
            bucket__project=project,
            bucket__transaction=transaction_name,
            bucket__hour__gte=_hour(starts_at),
            bucket__hour__lt=ends_at,
        )
        .values("op")
        .annotate(count=Sum("count"), total=Sum("duration_sum"))
        .order_by("-total")
    )
    return [(row["op"], row["count"], row["total"]) for row in rows]


def prune(before: datetime) -> int:
    removed, _ = TransactionBucket.objects.filter(hour__lt=_hour(before)).delete()
    return removed
