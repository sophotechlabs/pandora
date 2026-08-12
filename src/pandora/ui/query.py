from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db.models import Q, QuerySet

from pandora.issues import triage
from pandora.issues.models import Issue, Level, SourceState, TriageState

DEFAULT_QUERY = "is:unresolved"
UNRESOLVED = "unresolved"

KEY_NAME = re.compile(r"^[a-z_]+$")
LABEL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*(_[A-Za-z0-9]+)*$")
DURATION = re.compile(r"^(\d+)([mhdw])$")

DURATION_UNITS = {
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}

ALIASES = {
    "env": "environment",
    "status": "state",
}

TRIAGE_VALUES = {
    "new": TriageState.NEW,
    "acknowledged": TriageState.ACKNOWLEDGED,
    "ack": TriageState.ACKNOWLEDGED,
    "resolved": TriageState.RESOLVED,
    "ignored": TriageState.IGNORED,
}

Handler = Callable[
    [QuerySet[Issue], Sequence[str], datetime],
    tuple[QuerySet[Issue], list[str]],
]


@dataclass(frozen=True)
class Query:
    text: str = ""
    terms: tuple[tuple[str, str], ...] = ()
    unknown: tuple[str, ...] = field(default_factory=tuple)


def parse(raw: str) -> Query:
    terms: list[tuple[str, str]] = []
    words: list[str] = []
    unknown: list[str] = []

    for token in _split(raw):
        raw_key, separator, value = token.partition(":")
        key = ALIASES.get(raw_key.lower(), raw_key.lower())
        if not separator or not value:
            words.append(token)
            continue
        if key in HANDLERS:
            terms.append((key, value))
            continue
        if KEY_NAME.match(raw_key):
            unknown.append(token)
            continue
        words.append(token)

    return Query(
        text=" ".join(words).strip(),
        terms=tuple(terms),
        unknown=tuple(unknown),
    )


def filter_issues(
    queryset: QuerySet[Issue], query: Query, now: datetime
) -> tuple[QuerySet[Issue], list[str]]:
    rejected: list[str] = []
    for key, values in _group(query.terms).items():
        queryset, bad = HANDLERS[key](queryset, values, now)
        rejected.extend(bad)
    if query.text:
        queryset = queryset.filter(_text_query(query.text))
    return queryset, rejected


def _split(raw: str) -> list[str]:
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def _group(terms: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for key, value in terms:
        grouped.setdefault(key, []).append(value)
    return grouped


def _text_query(text: str) -> Q:
    return (
        Q(title__icontains=text)
        | Q(culprit__icontains=text)
        | Q(fingerprint_hash__startswith=text)
    )


def _apply_is(
    queryset: QuerySet[Issue], values: Sequence[str], now: datetime
) -> tuple[QuerySet[Issue], list[str]]:
    query = Q()
    rejected = []
    for value in values:
        if value == UNRESOLVED:
            query |= Q(triage_state__in=triage.OPEN_STATES)
            continue
        state = TRIAGE_VALUES.get(value.lower())
        if state is None:
            rejected.append(f"is:{value}")
            continue
        query |= Q(triage_state=state)
    return queryset.filter(query), rejected


def _apply_state(
    queryset: QuerySet[Issue], values: Sequence[str], now: datetime
) -> tuple[QuerySet[Issue], list[str]]:
    query = Q()
    rejected = []
    for value in values:
        if value.lower() not in SourceState.values:
            rejected.append(f"state:{value}")
            continue
        query |= Q(source_state=value.lower())
    return queryset.filter(query), rejected


def _apply_level(
    queryset: QuerySet[Issue], values: Sequence[str], now: datetime
) -> tuple[QuerySet[Issue], list[str]]:
    query = Q()
    rejected = []
    for value in values:
        if value.lower() not in Level.values:
            rejected.append(f"level:{value}")
            continue
        query |= Q(level=value.lower())
    return queryset.filter(query), rejected


def _apply_project(
    queryset: QuerySet[Issue], values: Sequence[str], now: datetime
) -> tuple[QuerySet[Issue], list[str]]:
    return queryset.filter(project__slug__in=list(values)), []


def _apply_environment(
    queryset: QuerySet[Issue], values: Sequence[str], now: datetime
) -> tuple[QuerySet[Issue], list[str]]:
    return queryset.filter(environment__in=list(values)), []


def _apply_seen(
    queryset: QuerySet[Issue], values: Sequence[str], now: datetime
) -> tuple[QuerySet[Issue], list[str]]:
    return _apply_window(queryset, values, now, "seen", "last_seen__gte")


def _apply_age(
    queryset: QuerySet[Issue], values: Sequence[str], now: datetime
) -> tuple[QuerySet[Issue], list[str]]:
    return _apply_window(queryset, values, now, "age", "first_seen__gte")


def _apply_window(
    queryset: QuerySet[Issue],
    values: Sequence[str],
    now: datetime,
    key: str,
    lookup: str,
) -> tuple[QuerySet[Issue], list[str]]:
    query = Q()
    rejected = []
    for value in values:
        window = parse_duration(value)
        if window is None:
            rejected.append(f"{key}:{value}")
            continue
        query |= Q(**{lookup: now - window})
    return queryset.filter(query), rejected


def _apply_label(
    queryset: QuerySet[Issue], values: Sequence[str], now: datetime
) -> tuple[QuerySet[Issue], list[str]]:
    rejected = []
    for value in values:
        name, separator, wanted = value.partition("=")
        if not separator or not LABEL_NAME.match(name):
            rejected.append(f"label:{value}")
            continue
        queryset = queryset.filter(**{f"grouping_labels__{name}": wanted})
    return queryset, rejected


def _apply_tag(
    queryset: QuerySet[Issue], values: Sequence[str], now: datetime
) -> tuple[QuerySet[Issue], list[str]]:
    rejected = []
    for value in values:
        name, separator, wanted = value.partition("=")
        if not separator:
            rejected.append(f"tag:{value}")
            continue
        queryset = queryset.filter(tag_stats__key=name, tag_stats__value=wanted)
    return queryset, rejected


def parse_duration(raw: str) -> timedelta | None:
    match = DURATION.match(raw.strip().lower())
    if match is None:
        return None
    amount, unit = match.groups()
    return timedelta(**{DURATION_UNITS[unit]: int(amount)})


HANDLERS: dict[str, Handler] = {
    "is": _apply_is,
    "state": _apply_state,
    "level": _apply_level,
    "project": _apply_project,
    "environment": _apply_environment,
    "seen": _apply_seen,
    "age": _apply_age,
    "label": _apply_label,
    "tag": _apply_tag,
}
