from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from django.db.models import F, QuerySet

from pandora.issues.models import (
    TAG_OVERFLOW_VALUE,
    TAG_VALUE_CAP,
    Episode,
    HourlyStat,
    Issue,
    TagStat,
)

KEY_MAX = 200
VALUE_MAX = 500


def hour_of(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def count_occurrence(issue: Issue, moment: datetime, tags: Mapping[str, str]) -> None:
    _bump_hour(issue, hour_of(moment))
    for key, value in tags.items():
        _bump_tag(issue, key[:KEY_MAX], value[:VALUE_MAX])


def rebuild(issue: Issue, episodes: Iterable[Episode]) -> None:
    hours: Counter[datetime] = Counter()
    tags: Counter[tuple[str, str]] = Counter()
    for episode in episodes:
        hours[hour_of(episode.starts_at)] += 1
        for key, value in episode.labels.items():
            tags[(str(key)[:KEY_MAX], str(value)[:VALUE_MAX])] += 1

    HourlyStat.objects.filter(issue=issue).delete()
    TagStat.objects.filter(issue=issue).delete()
    HourlyStat.objects.bulk_create(
        [
            HourlyStat(issue=issue, hour=hour, count=count)
            for hour, count in sorted(hours.items())
        ]
    )
    TagStat.objects.bulk_create(
        [
            TagStat(issue=issue, key=key, value=value, count=count)
            for (key, value), count in sorted(_capped(tags).items())
        ]
    )


def _capped(tags: Counter[tuple[str, str]]) -> dict[tuple[str, str], int]:
    by_key: dict[str, list[tuple[str, int]]] = {}
    for (key, value), count in tags.items():
        by_key.setdefault(key, []).append((value, count))

    capped: dict[tuple[str, str], int] = {}
    for key, values in by_key.items():
        values.sort(key=lambda pair: (-pair[1], pair[0]))
        for value, count in values[: TAG_VALUE_CAP - 1]:
            capped[(key, value)] = count
        overflow = sum(count for _, count in values[TAG_VALUE_CAP - 1 :])
        if overflow > 0:
            capped[(key, TAG_OVERFLOW_VALUE)] = overflow
    return capped


def _bump_hour(issue: Issue, hour: datetime) -> None:
    if _increment(HourlyStat.objects.filter(issue=issue, hour=hour)):
        return
    HourlyStat.objects.create(issue=issue, hour=hour, count=1)


def _bump_tag(issue: Issue, key: str, value: str) -> None:
    if _increment(TagStat.objects.filter(issue=issue, key=key, value=value)):
        return
    if TagStat.objects.filter(issue=issue, key=key).count() >= TAG_VALUE_CAP:
        value = TAG_OVERFLOW_VALUE
        if _increment(TagStat.objects.filter(issue=issue, key=key, value=value)):
            return
    TagStat.objects.create(issue=issue, key=key, value=value, count=1)


def _increment(rows: QuerySet[Any]) -> bool:
    return rows.update(count=F("count") + 1) > 0
