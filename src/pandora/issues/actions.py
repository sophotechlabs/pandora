from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db import transaction

from pandora.am import client as am_client
from pandora.am import silences
from pandora.issues import hooks, triage
from pandora.issues import snooze as snooze_module
from pandora.issues.models import ActivityKind, Issue, IssueActivity

SILENCE_WINDOWS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}

TRIAGE_VERBS = {
    triage.ACKNOWLEDGED: "Acknowledged",
    triage.RESOLVED: "Resolved",
    triage.IGNORED: "Ignored",
}


@dataclass(frozen=True)
class TriageReport:
    changed: int = 0
    unchanged: int = 0


@dataclass(frozen=True)
class SilenceReport:
    silenced: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


def apply_triage(issue: Issue, target_state: str, actor: str, at: datetime) -> bool:
    plan = triage.plan_triage(issue.triage_state, target_state, at)
    if not plan.changed:
        return False
    resolving = target_state == triage.RESOLVED

    previous_state = issue.triage_state
    with transaction.atomic():
        for name, value in plan.issue_fields.items():
            setattr(issue, name, value)
        issue.needs_review = False
        issue.escalated_at = None
        issue.save(update_fields=[*plan.issue_fields, "needs_review", "escalated_at"])
        IssueActivity.objects.create(
            issue=issue,
            kind=plan.activity_kind,
            actor=actor,
            at=at,
            data={"previous_triage_state": previous_state},
        )
    if resolving:
        hooks.fire("PANDORA_RESOLVE_HOOKS", issue, actor)
    return True


def retriage(
    issues: Iterable[Issue], target_state: str, actor: str, at: datetime
) -> TriageReport:
    changed = 0
    total = 0
    for issue in issues:
        total += 1
        if apply_triage(issue, target_state, actor, at):
            changed += 1
    return TriageReport(changed=changed, unchanged=total - changed)


def mark_reviewed(issues: Iterable[Issue], actor: str, at: datetime) -> TriageReport:
    """Take an issue out of the review queue without deciding anything else.

    A reader who has looked at a new issue and wants it to stay open needs a way
    to say so; without one the queue only empties by resolving things.
    """
    changed = 0
    total = 0
    for issue in issues:
        total += 1
        if not issue.needs_review:
            continue
        with transaction.atomic():
            issue.needs_review = False
            issue.save(update_fields=["needs_review"])
            IssueActivity.objects.create(
                issue=issue,
                kind=ActivityKind.REVIEWED,
                actor=actor,
                at=at,
            )
        changed += 1
    return TriageReport(changed=changed, unchanged=total - changed)


def set_priority(
    issues: Iterable[Issue], value: str, actor: str, at: datetime
) -> TriageReport:
    """Pin a rank a person disagreed with, so the sweep stops overwriting it."""
    changed = 0
    total = 0
    for issue in issues:
        total += 1
        if issue.priority == value and issue.priority_locked:
            continue
        previous = issue.priority
        with transaction.atomic():
            issue.priority = value
            issue.priority_locked = True
            issue.save(update_fields=["priority", "priority_locked"])
            IssueActivity.objects.create(
                issue=issue,
                kind=ActivityKind.REPRIORITISED,
                actor=actor,
                at=at,
                data={"previous_priority": previous, "priority": value},
            )
        changed += 1
    return TriageReport(changed=changed, unchanged=total - changed)


def silence(
    issues: Iterable[Issue],
    duration: timedelta,
    actor: str,
    client: am_client.AlertmanagerClient,
) -> SilenceReport:
    silenced = 0
    errors = []
    for issue in issues:
        try:
            silences.silence_issue(issue, duration, actor=actor, client=client)
        except (silences.SilenceError, am_client.AlertmanagerError) as error:
            errors.append(f"{issue.title} was not silenced — {error}")
            continue
        silenced += 1
    return SilenceReport(silenced=silenced, errors=tuple(errors))


@dataclass(frozen=True)
class SnoozeReport:
    snoozed: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


def apply_snooze(
    issues: Iterable[Issue], spec: str, actor: str, at: datetime
) -> SnoozeReport:
    snoozed = 0
    errors: list[str] = []
    for issue in issues:
        plan = snooze_module.plan(issue, spec, at)
        if plan.error:
            return SnoozeReport(errors=(plan.error,))
        with transaction.atomic():
            issue.snoozed_until = plan.until
            issue.snoozed_past_count = plan.past_count
            issue.save(update_fields=["snoozed_until", "snoozed_past_count"])
            IssueActivity.objects.create(
                issue=issue,
                kind="snoozed",
                actor=actor,
                at=at,
                data={"spec": spec},
            )
        snoozed += 1
    return SnoozeReport(snoozed=snoozed, errors=tuple(errors))


def wake(issue: Issue, at: datetime) -> bool:
    if not snooze_module.expired(issue, at):
        return False
    with transaction.atomic():
        issue.snoozed_until = None
        issue.snoozed_past_count = None
        issue.save(update_fields=["snoozed_until", "snoozed_past_count"])
        IssueActivity.objects.create(issue=issue, kind="unsnoozed", at=at)
    hooks.fire("PANDORA_WAKE_HOOKS", issue)
    return True
