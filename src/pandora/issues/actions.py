from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db import transaction

from pandora.am import client as am_client
from pandora.am import silences
from pandora.issues import snooze as snooze_module
from pandora.issues import triage
from pandora.issues.models import Issue, IssueActivity

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

    previous_state = issue.triage_state
    with transaction.atomic():
        for name, value in plan.issue_fields.items():
            setattr(issue, name, value)
        issue.save(update_fields=list(plan.issue_fields))
        IssueActivity.objects.create(
            issue=issue,
            kind=plan.activity_kind,
            actor=actor,
            at=at,
            data={"previous_triage_state": previous_state},
        )
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
    return True
