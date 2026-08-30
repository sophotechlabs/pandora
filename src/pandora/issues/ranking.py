from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Count, F, Q, QuerySet, Sum
from django.db.models.functions import Coalesce

from pandora.issues.models import HourlyStat, Issue

RECENT = timedelta(hours=24)
WINDOW = timedelta(days=7)
RECENT_WEIGHT = 4
BASELINE = timedelta(days=7)
MIN_RATE = 0.01


def breadth_keys() -> tuple[str, ...]:
    raw = settings.PANDORA_BREADTH_KEYS or ""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def with_score(queryset: QuerySet[Issue], now: datetime) -> QuerySet[Issue]:
    """Rank by what is happening, not by what happened last.

    An issue seen four hundred times last month sits below one seen forty times
    this morning. Two sums over the hourly buckets already on disk, weighted
    toward the last day — no new storage, and it orders in the database so the
    stream can still page.
    """
    recent = _hour(now - RECENT)
    window = _hour(now - WINDOW)
    return queryset.annotate(
        recent_count=Coalesce(
            Sum("hourly_stats__count", filter=Q(hourly_stats__hour__gte=recent)),
            0,
        ),
        window_count=Coalesce(
            Sum("hourly_stats__count", filter=Q(hourly_stats__hour__gte=window)),
            0,
        ),
    ).annotate(score=F("recent_count") * RECENT_WEIGHT + F("window_count"))


def with_breadth(queryset: QuerySet[Issue]) -> QuerySet[Issue]:
    """How many distinct places the issue has been seen.

    *Is it everyone or one node* is the first question in an incident, and a
    count over the tag breakdown already on disk is its cheapest answer.
    """
    keys = breadth_keys()
    return queryset.annotate(
        breadth=Count(
            "tag_stats__value",
            filter=Q(tag_stats__key__in=keys),
            distinct=True,
        )
    )


def rate_change(issue: Issue, now: datetime) -> float:
    """The issue's rate over the last day against its own previous week.

    One means unchanged. Shared with the alert-error join, which asks the same
    question of a different set of issues.
    """
    recent_hours = RECENT.total_seconds() / 3600
    baseline_hours = BASELINE.total_seconds() / 3600
    recent = _total(issue, _hour(now - RECENT), _hour(now))
    baseline = _total(issue, _hour(now - RECENT - BASELINE), _hour(now - RECENT))
    rate = recent / recent_hours
    before = max(baseline / baseline_hours, MIN_RATE)
    return rate / before


def _total(issue: Issue, starts_at: datetime, ends_at: datetime) -> int:
    row = HourlyStat.objects.filter(
        issue=issue, hour__gte=starts_at, hour__lt=ends_at
    ).aggregate(total=Sum("count"))
    return row["total"] or 0


def _hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)
