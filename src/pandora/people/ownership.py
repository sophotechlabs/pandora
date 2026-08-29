from __future__ import annotations

import fnmatch
from collections.abc import Iterable

from django.db import models

from pandora.events.types import Event
from pandora.issues.lifecycle import Occurrence
from pandora.issues.models import Issue
from pandora.people.models import Assignment, OwnershipRule

PATH = "path"
URL = "url"
CULPRIT = "culprit"
TAG = "tag"
FIELDS = (PATH, URL, CULPRIT, TAG)


def candidates(issue: Issue, event: Event | Occurrence | None) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {name: set() for name in FIELDS}
    found[CULPRIT].add(issue.culprit)
    for key, value in (issue.grouping_labels or {}).items():
        found[TAG].add(f"{key}={value}")
    if event is None:
        return found
    payload = event.payload or {}
    for exception in payload.get("exceptions", []) or []:
        for frame in exception.get("frames", []) or []:
            for name in ("filename", "abs_path", "module"):
                value = frame.get(name)
                if value:
                    found[PATH].add(str(value))
    request = payload.get("request") or {}
    if request.get("url"):
        found[URL].add(str(request["url"]))
    for key, value in (event.tags or {}).items():
        found[TAG].add(f"{key}={value}")
    return found


def rules_for(issue: Issue) -> list[OwnershipRule]:
    return list(
        OwnershipRule.objects.filter(active=True)
        .filter(models.Q(project=None) | models.Q(project_id=issue.project_id))
        .select_related("team", "user")
    )


def matching(issue: Issue, event: Event | Occurrence | None) -> list[OwnershipRule]:
    values = candidates(issue, event)
    matched = []
    for rule in rules_for(issue):
        haystack = values.get(rule.field, set())
        if any(fnmatch.fnmatchcase(value, rule.pattern) for value in haystack):
            matched.append(rule)
    return matched


def assign(issue: Issue, event: Event | Occurrence | None) -> Assignment | None:
    matched = matching(issue, event)
    if len(matched) != 1:
        return None
    rule = matched[0]
    assignment, _ = Assignment.objects.update_or_create(
        issue=issue,
        defaults={"team": rule.team, "user": rule.user, "rule": rule},
    )
    return assignment


def suggestions(issue: Issue, event: Event | Occurrence | None) -> list[OwnershipRule]:
    matched = matching(issue, event)
    if len(matched) == 1:
        return []
    return matched


def owners_of(issues: Iterable[Issue]) -> dict[int, Assignment]:
    rows = Assignment.objects.filter(issue__in=list(issues)).select_related(
        "team", "user", "rule"
    )
    return {row.issue_id: row for row in rows}
