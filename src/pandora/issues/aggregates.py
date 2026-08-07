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
    _bump_tags(issue, [(k[:KEY_MAX], v[:VALUE_MAX]) for k, v in tags.items()])


def rebuild(issue: Issue, episodes: Iterable[Episode]) -> None:
    rebuild_from(issue, ((episode.starts_at, episode.labels) for episode in episodes))


def rebuild_from(
    issue: Issue, samples: Iterable[tuple[datetime, Mapping[str, Any]]]
) -> None:
    hours: Counter[datetime] = Counter()
    tags: Counter[tuple[str, str]] = Counter()
    for moment, labels in samples:
        hours[hour_of(moment)] += 1
        for key, value in labels.items():
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
        if _unbounded(values):
            capped[(key, TAG_OVERFLOW_VALUE)] = sum(count for _, count in values)
            continue
        values.sort(key=lambda pair: (-pair[1], pair[0]))
        for value, count in values[: TAG_VALUE_CAP - 1]:
            capped[(key, value)] = count
        overflow = sum(count for _, count in values[TAG_VALUE_CAP - 1 :])
        if overflow > 0:
            capped[(key, TAG_OVERFLOW_VALUE)] = overflow
    return capped


def _unbounded(values: list[tuple[str, int]]) -> bool:
    if len(values) <= TAG_VALUE_CAP:
        return False
    return all(count == 1 for _, count in values)


def _bump_hour(issue: Issue, hour: datetime) -> None:
    if _increment(HourlyStat.objects.filter(issue=issue, hour=hour)):
        return
    HourlyStat.objects.create(issue=issue, hour=hour, count=1)


def _bump_tags(issue: Issue, pairs: list[tuple[str, str]]) -> None:
    if not pairs:
        return

    keys = {key for key, _ in pairs}
    known = list(
        TagStat.objects.filter(issue=issue, key__in=keys).values_list(
            "pk", "key", "value", "count"
        )
    )
    by_pair = {(key, value): pk for pk, key, value, _ in known}
    per_key = Counter(key for _, key, _, _ in known)
    folded = _fold_unbounded(issue, known, pairs, by_pair, per_key)

    hits: list[int] = []
    fresh: Counter[tuple[str, str]] = Counter(
        {(key, TAG_OVERFLOW_VALUE): total for key, total in folded.items()}
    )
    for key, value in pairs:
        target = _tag_target(key, value, by_pair, per_key, folded)
        known_pk = by_pair.get(target)
        if known_pk is not None:
            hits.append(known_pk)
            continue
        if not fresh[target]:
            per_key[key] += 1
        fresh[target] += 1

    if hits:
        TagStat.objects.filter(pk__in=hits).update(count=F("count") + 1)
    if fresh:
        TagStat.objects.bulk_create(
            [
                TagStat(issue=issue, key=key, value=value, count=count)
                for (key, value), count in sorted(fresh.items())
            ],
            ignore_conflicts=True,
        )


def _fold_unbounded(
    issue: Issue,
    known: list[tuple[int, str, str, int]],
    pairs: list[tuple[str, str]],
    by_pair: dict[tuple[str, str], int],
    per_key: Counter[str],
) -> dict[str, int]:
    folded: dict[str, int] = {}
    for key in _overflowing(pairs, by_pair, per_key):
        rows = [row for row in known if row[1] == key]
        if any(count > 1 for _, _, value, count in rows if value != TAG_OVERFLOW_VALUE):
            continue
        TagStat.objects.filter(pk__in=[pk for pk, _, _, _ in rows]).delete()
        for _, _, value, _ in rows:
            by_pair.pop((key, value), None)
        per_key[key] = 0
        folded[key] = sum(count for _, _, _, count in rows)
    return folded


def _overflowing(
    pairs: list[tuple[str, str]],
    by_pair: dict[tuple[str, str], int],
    per_key: Counter[str],
) -> set[str]:
    found = set()
    for key, value in pairs:
        if (key, value) in by_pair:
            continue
        if per_key[key] < TAG_VALUE_CAP:
            continue
        found.add(key)
    return found


def _tag_target(
    key: str,
    value: str,
    by_pair: dict[tuple[str, str], int],
    per_key: Counter[str],
    folded: dict[str, int],
) -> tuple[str, str]:
    if (key, value) in by_pair:
        return (key, value)
    if key in folded:
        return (key, TAG_OVERFLOW_VALUE)
    if (key, TAG_OVERFLOW_VALUE) in by_pair:
        return (key, TAG_OVERFLOW_VALUE)
    if per_key[key] >= TAG_VALUE_CAP:
        return (key, TAG_OVERFLOW_VALUE)
    return (key, value)


def _increment(rows: QuerySet[Any]) -> bool:
    return rows.update(count=F("count") + 1) > 0
