from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pandora.issues.models import Issue, TagStat

MIN_MEMBERS = 2


@dataclass(frozen=True)
class Suggestion:
    """A grouping rule that would have made the merge unnecessary.

    A merge is a labelled example: a person has said these occurrences are one
    fault. What they have in common is the rule, and what they differ on is the
    part of the key that should not have been in it.
    """

    conditions: dict[str, Any]
    fingerprint: list[str]
    shared: dict[str, str] = field(default_factory=dict)
    differing: list[str] = field(default_factory=list)

    def as_rule_fields(self) -> dict[str, Any]:
        return {"conditions": self.conditions, "fingerprint": self.fingerprint}


def for_issues(issues: Sequence[Issue]) -> Suggestion | None:
    return from_labels([labels_of(issue) for issue in issues])


def labels_of(issue: Issue) -> dict[str, str]:
    return _labels(issue)


def from_labels(members: Sequence[dict[str, str]]) -> Suggestion | None:
    if len(members) < MIN_MEMBERS:
        return None
    shared = _shared(members)
    differing = _differing(members)
    if not shared:
        return None
    return Suggestion(
        conditions=_conditions(shared),
        fingerprint=[f"{key}:{value}" for key, value in sorted(shared.items())],
        shared=shared,
        differing=differing,
    )


def _shared(members: Sequence[dict[str, str]]) -> dict[str, str]:
    first = members[0]
    return {
        key: value
        for key, value in first.items()
        if all(other.get(key) == value for other in members[1:])
    }


def _differing(members: Sequence[dict[str, str]]) -> list[str]:
    keys: set[str] = set()
    for member in members:
        keys.update(member)
    return [
        key for key in sorted(keys) if len({member.get(key) for member in members}) > 1
    ]


def _labels(issue: Issue) -> dict[str, str]:
    labels = dict(issue.grouping_labels or {})
    if labels:
        return labels
    return _from_tags(issue)


def _from_tags(issue: Issue) -> dict[str, str]:
    rows = TagStat.objects.filter(issue=issue).order_by("key", "-count")
    found: dict[str, str] = {}
    for row in rows:
        found.setdefault(row.key, row.value)
    return found


def _conditions(shared: dict[str, str]) -> dict[str, Any]:
    leaves: list[dict[str, Any]] = [
        {"path": f"labels.{key}", "op": "eq", "value": value}
        for key, value in sorted(shared.items())
    ]
    if len(leaves) == 1:
        return leaves[0]
    return {"all": leaves}
