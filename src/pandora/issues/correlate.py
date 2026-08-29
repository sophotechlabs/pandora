from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Sum

from pandora.issues.models import HourlyStat, Issue

DEFAULT_KEYS = ("namespace", "pod", "node", "cluster", "service")
ALIASES = {"server_name": "pod", "instance": "pod"}
CANDIDATE_CAP = 50
ROW_CAP = 5
BASELINE = timedelta(days=7)
MIN_RATIO = 1.5
MIN_COUNT = 3
MIN_RATE = 1.0


@dataclass(frozen=True)
class Match:
    issue: Issue
    shared: tuple[tuple[str, str], ...]
    count: int
    rate: float
    baseline: float
    ratio: float


@dataclass(frozen=True)
class Correlation:
    starts_at: datetime
    ends_at: datetime
    keys: tuple[str, ...]
    matches: tuple[Match, ...]


def keys() -> tuple[str, ...]:
    raw = settings.PANDORA_CORRELATION_KEYS
    parsed = tuple(part.strip() for part in raw.split(",") if part.strip())
    if parsed:
        return parsed
    return DEFAULT_KEYS


def window(issue: Issue, now: datetime) -> tuple[datetime, datetime]:
    episode = issue.episodes.order_by("-starts_at").first()
    if episode is not None:
        if episode.ends_at is None:
            return (episode.starts_at, now)
        return (episode.starts_at, episode.ends_at)
    margin = timedelta(minutes=settings.PANDORA_CORRELATION_WINDOW_MINUTES)
    return (issue.first_seen - margin, issue.first_seen + margin)


def labels(issue: Issue, wanted: Iterable[str]) -> dict[str, set[str]]:
    allowed = set(wanted)
    found: dict[str, set[str]] = {}
    for key, value in (issue.grouping_labels or {}).items():
        _add(found, allowed, key, str(value))
    for stat in issue.tag_stats.all():
        _add(found, allowed, stat.key, stat.value)
    return found


def _add(found: dict[str, set[str]], allowed: set[str], key: str, value: str) -> None:
    canonical = ALIASES.get(key, key)
    if canonical not in allowed:
        return
    if not value:
        return
    found.setdefault(canonical, set()).add(value)


def shared(left: Mapping[str, set[str]], right: Mapping[str, set[str]]) -> tuple:
    pairs = []
    for key in sorted(left):
        overlap = left[key] & right.get(key, set())
        for value in sorted(overlap):
            pairs.append((key, value))
    return tuple(pairs)


def build(issue: Issue, now: datetime) -> Correlation:
    starts_at, ends_at = window(issue, now)
    wanted = keys()
    mine = labels(issue, wanted)
    if not mine:
        return Correlation(starts_at, ends_at, wanted, ())

    counts = _counts(issue, starts_at, ends_at)
    if not counts:
        return Correlation(starts_at, ends_at, wanted, ())

    baselines = _baselines(issue, list(counts), starts_at)
    candidates = (
        Issue.objects.filter(pk__in=list(counts))
        .select_related("project")
        .prefetch_related("tag_stats")
    )

    hours = max((ends_at - starts_at).total_seconds() / 3600, 1.0)
    matches = []
    for candidate in candidates:
        pairs = shared(mine, labels(candidate, wanted))
        if not pairs:
            continue
        count = counts[candidate.pk]
        if count < MIN_COUNT:
            continue
        baseline = baselines.get(candidate.pk, 0.0)
        rate = count / hours
        ratio = rate / max(baseline, MIN_RATE)
        if ratio < MIN_RATIO:
            continue
        matches.append(
            Match(
                issue=candidate,
                shared=pairs,
                count=count,
                rate=rate,
                baseline=baseline,
                ratio=ratio,
            )
        )
    matches.sort(key=lambda match: (-match.ratio, -match.count, match.issue.pk))
    return Correlation(starts_at, ends_at, wanted, tuple(matches[:ROW_CAP]))


def _counts(issue: Issue, starts_at: datetime, ends_at: datetime) -> dict[int, int]:
    rows = (
        HourlyStat.objects.filter(
            issue__project_id=issue.project_id,
            hour__gte=starts_at.replace(minute=0, second=0, microsecond=0),
            hour__lt=ends_at,
        )
        .exclude(issue_id=issue.pk)
        .values("issue_id")
        .annotate(total=Sum("count"))
        .order_by("-total")[:CANDIDATE_CAP]
    )
    return {row["issue_id"]: row["total"] for row in rows}


def _baselines(
    issue: Issue, issue_ids: list[int], starts_at: datetime
) -> dict[int, float]:
    hours = BASELINE.total_seconds() / 3600
    rows = (
        HourlyStat.objects.filter(
            issue_id__in=issue_ids,
            hour__gte=starts_at - BASELINE,
            hour__lt=starts_at,
        )
        .values("issue_id")
        .annotate(total=Sum("count"))
    )
    return {row["issue_id"]: row["total"] / hours for row in rows}
