from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from typing import Any

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from pandora.events.types import Event
from pandora.issues import comments as comment_service
from pandora.issues.lifecycle import Occurrence
from pandora.issues.models import Issue, SubscriptionReason
from pandora.people.models import Assignment, OwnershipRule
from pandora.releases import commits as commit_service

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
    if len(matched) == 1:
        rule = matched[0]
        assignment, _ = Assignment.objects.update_or_create(
            issue=issue,
            defaults={"team": rule.team, "user": rule.user, "rule": rule},
        )
        _watch(issue, rule.user)
        return assignment
    return assign_from_commit(issue, event)


def _watch(issue: Issue, user: Any) -> None:
    if user is None:
        return
    comment_service.subscribe(issue, user, SubscriptionReason.ASSIGNED, timezone.now())


def author_of(issue: Issue, event: Event | Occurrence | None) -> Any:
    """The person whose commit last touched the code in the stack trace.

    A weaker signal than an ownership rule, so it only runs when no rule
    matched, and it stays silent unless the address maps to an account that
    already exists.
    """
    if event is None:
        return None
    frames = commit_service.frames_from(event.payload or {})
    if not frames:
        return None
    found = commit_service.suspects(issue.project, frames, issue.first_seen, limit=1)
    if not found:
        return None
    email = found[0].commit.author_email.strip()
    if not email:
        return None
    return get_user_model().objects.filter(email__iexact=email, is_active=True).first()


def assign_from_commit(
    issue: Issue, event: Event | Occurrence | None
) -> Assignment | None:
    user = author_of(issue, event)
    if user is None:
        return None
    assignment, _ = Assignment.objects.update_or_create(
        issue=issue,
        defaults={"team": None, "user": user, "rule": None},
    )
    _watch(issue, user)
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
