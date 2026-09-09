from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import NamedTuple

from django.db.models import F, Q, QuerySet

from pandora.issues import priority as priority_module
from pandora.issues import triage
from pandora.issues.models import Issue, Level, Priority, SourceState, TriageState

DEFAULT_QUERY = "is:unresolved"
SNOOZED = "snoozed"
AWAKE = "awake"
UNRESOLVED = "unresolved"
FOR_REVIEW = "for_review"
REVIEWED = "reviewed"
OWNER = "owner"
HAS = "has"
TAG = "tag"
ME = "me"
NOBODY = "none"
NEGATE = "!"
WILDCARD = "*"

KEY_NAME = re.compile(r"^[a-z_]+$")
LABEL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*(_[A-Za-z0-9]+)*$")
DURATION = re.compile(r"^(\d+)([mhdw])$")
TAG_SYNTAX = re.compile(r"^tags\[([^\]]+)\]$", re.IGNORECASE)
COMPARISON = re.compile(r"^(>=|<=|>|<)?\s*(\d+)$")

DURATION_UNITS = {
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}

COMPARISONS = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte", "": "exact"}

ALIASES = {
    "env": "environment",
    "status": "state",
    "assigned": "owner",
    "count": "events",
    "timesseen": "events",
    "users": "users",
}

TAG_KEYS = (
    "release",
    "dist",
    "trace",
    "url",
    "method",
    "browser",
    "os",
    "runtime",
    "device",
    "user",
    "logger",
    "handled",
    "mechanism",
    "server_name",
    "transaction",
)

JOIN_KEYS = ("environment", TAG, HAS, *TAG_KEYS)

TRIAGE_VALUES = {
    "new": TriageState.NEW,
    "acknowledged": TriageState.ACKNOWLEDGED,
    "ack": TriageState.ACKNOWLEDGED,
    "resolved": TriageState.RESOLVED,
    "ignored": TriageState.IGNORED,
}

Handler = Callable[
    [Sequence[str], datetime],
    tuple[Q, list[str]],
]


class Term(NamedTuple):
    key: str
    value: str
    negated: bool = False


@dataclass(frozen=True)
class Query:
    text: str = ""
    terms: tuple[Term, ...] = ()
    unknown: tuple[str, ...] = field(default_factory=tuple)
    negated_text: tuple[str, ...] = field(default_factory=tuple)


def parse(raw: str) -> Query:
    terms: list[Term] = []
    words: list[str] = []
    unknown: list[str] = []
    negated_text: list[str] = []

    for token in _split(raw):
        negated = token.startswith(NEGATE)
        body = token[1:] if negated else token
        raw_key, separator, value = body.partition(":")
        key = _key_for(raw_key)
        if not separator or not value:
            _plain(body, negated, words, negated_text)
            continue
        tagged = TAG_SYNTAX.match(raw_key)
        if tagged is not None:
            terms.append(Term(TAG, f"{tagged.group(1)}={value}", negated))
            continue
        if key in HANDLERS:
            terms.extend(Term(key, item, negated) for item in _values(value))
            continue
        if KEY_NAME.match(raw_key):
            unknown.append(token)
            continue
        _plain(body, negated, words, negated_text)

    return Query(
        text=" ".join(words).strip(),
        terms=tuple(terms),
        unknown=tuple(unknown),
        negated_text=tuple(negated_text),
    )


def _plain(body: str, negated: bool, words: list[str], negated_text: list[str]) -> None:
    if negated:
        negated_text.append(body)
        return
    words.append(body)


def _key_for(raw_key: str) -> str:
    lowered = raw_key.lower()
    return ALIASES.get(lowered, lowered)


def _values(value: str) -> list[str]:
    text = value.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return [value]
    inner = text[1:-1]
    return [part.strip() for part in inner.split(",") if part.strip()]


def filter_issues(
    queryset: QuerySet[Issue],
    query: Query,
    now: datetime,
    viewer: str = "",
) -> tuple[QuerySet[Issue], list[str]]:
    rejected: list[str] = []
    joined = False
    for (key, negated), values in _group(query.terms).items():
        built, bad = HANDLERS[key](_resolve(key, values, viewer), now)
        rejected.extend(bad)
        if key in JOIN_KEYS:
            joined = True
        if negated:
            queryset = queryset.exclude(built)
            continue
        queryset = queryset.filter(built)
    if query.text:
        queryset = queryset.filter(_text_query(query.text))
    for text in query.negated_text:
        queryset = queryset.exclude(_text_query(text))
    if joined:
        return queryset.distinct(), rejected
    return queryset, rejected


def _resolve(key: str, values: list[str], viewer: str) -> list[str]:
    if key != OWNER:
        return values
    resolved = []
    for value in values:
        if value.lower() == ME and viewer:
            resolved.append(viewer)
        else:
            resolved.append(value)
    return resolved


def _split(raw: str) -> list[str]:
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def _group(terms: Iterable[Term]) -> dict[tuple[str, bool], list[str]]:
    grouped: dict[tuple[str, bool], list[str]] = {}
    for term in terms:
        grouped.setdefault((term.key, term.negated), []).append(term.value)
    return grouped


def match(field_name: str, value: str, default: str = "exact") -> Q:
    """Turn one search value into a lookup, honouring the * wildcard.

    Sentry's grammar is a glob, not a regular expression, so the parts around
    each star are escaped and only the star becomes a pattern. Without that a
    value like `C++` would be read as a broken regex.
    """
    if WILDCARD not in value:
        return Q(**{f"{field_name}__{default}": value})
    parts = value.split(WILDCARD)
    if len(parts) == 2:
        head, tail = parts
        if not head:
            return Q(**{f"{field_name}__iendswith": tail})
        if not tail:
            return Q(**{f"{field_name}__istartswith": head})
    if value.startswith(WILDCARD) and value.endswith(WILDCARD) and len(parts) == 3:
        return Q(**{f"{field_name}__icontains": parts[1]})
    pattern = ".*".join(re.escape(part) for part in parts)
    return Q(**{f"{field_name}__iregex": f"^{pattern}$"})


def _text_query(text: str) -> Q:
    return (
        match("title", text, default="icontains")
        | match("culprit", text, default="icontains")
        | match("search_text", text, default="icontains")
        | Q(fingerprint_hash__startswith=text)
    )


def _snoozed_query(now: datetime) -> Q:
    return Q(snoozed_until__gt=now) | Q(snoozed_past_count__gt=F("event_count"))


def _apply_is(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    query = Q()
    rejected = []
    for value in values:
        lowered = value.lower()
        if lowered == UNRESOLVED:
            query |= Q(triage_state__in=triage.OPEN_STATES)
            continue
        if lowered == SNOOZED:
            query |= _snoozed_query(now)
            continue
        if lowered == AWAKE:
            query |= ~_snoozed_query(now)
            continue
        if lowered == FOR_REVIEW:
            query |= Q(needs_review=True)
            continue
        if lowered == REVIEWED:
            query |= Q(needs_review=False)
            continue
        state = TRIAGE_VALUES.get(lowered)
        if state is None:
            rejected.append(f"is:{value}")
            continue
        query |= Q(triage_state=state)
    return query, rejected


def _apply_state(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    query = Q()
    rejected = []
    for value in values:
        if value.lower() not in SourceState.values:
            rejected.append(f"state:{value}")
            continue
        query |= Q(source_state=value.lower())
    return query, rejected


def _apply_level(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    query = Q()
    rejected = []
    for value in values:
        if value.lower() not in Level.values:
            rejected.append(f"level:{value}")
            continue
        query |= Q(level=value.lower())
    return query, rejected


def _apply_priority(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    query = Q()
    rejected = []
    for value in values:
        wanted, above = _priority_value(value)
        if wanted is None:
            rejected.append(f"priority:{value}")
            continue
        if above:
            query |= Q(priority__in=priority_module.at_least(wanted))
            continue
        query |= Q(priority=wanted)
    return query, rejected


def _priority_value(raw: str) -> tuple[str | None, bool]:
    text = raw.strip().lower()
    above = text.startswith(">=")
    if above:
        text = text[2:].strip()
    if text not in Priority.values:
        return None, above
    return text, above


def _apply_project(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    query = Q()
    for value in values:
        query |= match("project__slug", value)
    return query, []


def _apply_environment(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    query = Q()
    for value in values:
        query |= match("environments__name", value)
    return query, []


def _apply_seen(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    return _apply_window(values, now, "seen", "last_seen__gte")


def _apply_age(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    return _apply_window(values, now, "age", "first_seen__gte")


def _apply_window(
    values: Sequence[str],
    now: datetime,
    key: str,
    lookup: str,
) -> tuple[Q, list[str]]:
    query = Q()
    rejected = []
    for value in values:
        window = parse_duration(value)
        if window is None:
            rejected.append(f"{key}:{value}")
            continue
        query |= Q(**{lookup: now - window})
    return query, rejected


def _apply_label(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    query = Q()
    rejected = []
    for value in values:
        name, separator, wanted = value.partition("=")
        if not separator or not LABEL_NAME.match(name):
            rejected.append(f"label:{value}")
            continue
        query &= Q(**{f"grouping_labels__{name}": wanted})
    return query, rejected


def _apply_tag(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    query = Q()
    rejected = []
    for value in values:
        name, separator, wanted = value.partition("=")
        if not separator:
            rejected.append(f"tag:{value}")
            continue
        query |= Q(tag_stats__key=name) & match("tag_stats__value", wanted)
    return query, rejected


def _tag_handler(name: str) -> Handler:
    def apply(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
        query = Q()
        for value in values:
            query |= Q(tag_stats__key=name) & match("tag_stats__value", value)
        return query, []

    return apply


def _apply_has(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    query = Q()
    for value in values:
        name = _key_for(value)
        if name == OWNER:
            query |= Q(assignment__isnull=False)
            continue
        if name == "environment":
            query |= Q(environments__isnull=False)
            continue
        query |= Q(tag_stats__key=name)
    return query, []


def _numeric_handler(field_name: str, key: str) -> Handler:
    def apply(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
        query = Q()
        rejected = []
        for value in values:
            match_ = COMPARISON.match(value.strip())
            if match_ is None:
                rejected.append(f"{key}:{value}")
                continue
            operator, number = match_.groups()
            lookup = COMPARISONS[operator or ""]
            query |= Q(**{f"{field_name}__{lookup}": int(number)})
        return query, rejected

    return apply


def parse_duration(raw: str) -> timedelta | None:
    match_ = DURATION.match(raw.strip().lower())
    if match_ is None:
        return None
    amount, unit = match_.groups()
    return timedelta(**{DURATION_UNITS[unit]: int(amount)})


def _apply_owner(values: Sequence[str], now: datetime) -> tuple[Q, list[str]]:
    query = Q()
    for value in values:
        if value.lower() == NOBODY:
            query |= Q(assignment__isnull=True)
            continue
        query |= match("assignment__team__name", value) | match(
            "assignment__user__username", value
        )
    return query, []


HANDLERS: dict[str, Handler] = {
    "is": _apply_is,
    "priority": _apply_priority,
    "state": _apply_state,
    "level": _apply_level,
    "project": _apply_project,
    "environment": _apply_environment,
    "seen": _apply_seen,
    "age": _apply_age,
    "label": _apply_label,
    TAG: _apply_tag,
    HAS: _apply_has,
    "events": _numeric_handler("event_count", "events"),
    "users": _numeric_handler("user_count", "users"),
    OWNER: _apply_owner,
}

for _name in TAG_KEYS:
    HANDLERS[_name] = _tag_handler(_name)
