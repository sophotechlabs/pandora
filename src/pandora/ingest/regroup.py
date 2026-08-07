from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from pandora.core.models import Project, TokenSource
from pandora.events.store import EventStore, get_store
from pandora.events.types import Event
from pandora.ingest.models import EnvelopeState, RawEnvelope
from pandora.ingest.translators import envelope as envelope_translator
from pandora.issues import aggregates, lifecycle
from pandora.issues.models import ActivityKind, Issue, IssueActivity

TEMP_PREFIX = "regroup-"
PAGE = 500

log = logging.getLogger(__name__)

Member = tuple[Event, lifecycle.Occurrence]
Row = tuple[Event, lifecycle.Occurrence | None]


@dataclass
class EventRegroupReport:
    projects: int = 0
    envelopes: int = 0
    unreadable: int = 0
    events: int = 0
    issues_before: int = 0
    issues_after: int = 0
    events_moved: int = 0
    issues_created: int = 0
    issues_renamed: int = 0
    issues_deleted: int = 0
    triage_migrated: int = 0
    orphans: list[str] = field(default_factory=list)


@dataclass
class _Group:
    digest: str
    environment: str
    fingerprint: list[str]
    members: list[Member]

    @property
    def key(self) -> tuple[str, str]:
        return (self.environment, self.digest)


@dataclass
class _Landing:
    group: _Group
    issue: Issue
    changed: bool


class _Rollback(Exception):
    pass


def regroup_events(
    project: Project | None = None,
    dry_run: bool = False,
    store: EventStore | None = None,
) -> EventRegroupReport:
    report = EventRegroupReport()
    if store is None:
        store = get_store()
    try:
        with transaction.atomic():
            _run(report, project, store)
            if dry_run:
                raise _Rollback
    except _Rollback:
        pass
    return report


def _run(
    report: EventRegroupReport, project: Project | None, store: EventStore
) -> None:
    projects = Project.objects.all()
    if project is not None:
        projects = Project.objects.filter(pk=project.pk)
    for row in projects.order_by("pk"):
        _regroup_project(report, row, store)


def _regroup_project(
    report: EventRegroupReport, project: Project, store: EventStore
) -> None:
    report.projects += 1
    fresh = _translated(report, project)
    if not fresh:
        return

    holders, owned = _holders(project, store)
    report.events += sum(len(events) for events in owned.values())
    report.issues_before += len(holders)

    groups = _groups(owned, fresh)
    if not groups:
        return

    by_hash = {
        (issue.environment, issue.fingerprint_hash): issue for issue in holders.values()
    }
    parked = {issue.pk: issue.fingerprint_hash for issue in holders.values()}
    _park_identities(holders)

    owner = _owners(owned)
    landings = _settle_groups(
        report, project, groups, holders=holders, by_hash=by_hash, owner=owner
    )
    _relink(report, project, store, landings, owner)

    final = _final_rows(owned, fresh, owner)
    _write_landings(landings, final)
    _write_holders(report, holders, landings, parked, final)
    report.issues_after += sum(1 for rows in final.values() if rows)


def _translated(
    report: EventRegroupReport, project: Project
) -> dict[str, lifecycle.Occurrence]:
    fresh: dict[str, lifecycle.Occurrence] = {}
    envelopes = RawEnvelope.objects.filter(
        project=project,
        source=TokenSource.SDK,
        state=EnvelopeState.DONE,
    ).order_by("pk")
    for envelope in envelopes.iterator():
        report.envelopes += 1
        occurrence = _translate(envelope, project)
        if occurrence is None:
            report.unreadable += 1
            continue
        sentry_id = envelope_translator.sentry_event_id(
            envelope.payload,
            fallback=f"envelope-{envelope.pk}",
        )
        stored_id = envelope_translator.event_id(
            project.pk, sentry_id, occurrence.starts_at
        )
        fresh[stored_id] = occurrence
    return fresh


def _translate(envelope: RawEnvelope, project: Project) -> lifecycle.Occurrence | None:
    try:
        return envelope_translator.translate_event(
            envelope.payload,
            project,
            environment=envelope.environment,
            received_at=envelope.received_at,
        )
    except envelope_translator.EnvelopeError:
        log.warning(
            "envelope %s cannot be re-read — its issue keeps the grouping it has",
            envelope.pk,
        )
        return None


def _holders(
    project: Project, store: EventStore
) -> tuple[dict[int, Issue], dict[int, list[Event]]]:
    holders: dict[int, Issue] = {}
    owned: dict[int, list[Event]] = {}
    candidates = Issue.objects.filter(project=project, episodes__isnull=True)
    for issue in candidates.order_by("pk"):
        events = _events_of(store, project.pk, issue.pk)
        if not events:
            continue
        holders[issue.pk] = issue
        owned[issue.pk] = events
    return holders, owned


def _events_of(store: EventStore, project_id: int, issue_id: int) -> list[Event]:
    found: list[Event] = []
    cursor = None
    while True:
        page = store.fetch(project_id, issue_id=issue_id, before=cursor, limit=PAGE)
        found.extend(page)
        if len(page) < PAGE:
            return found
        cursor = page[-1].id


def _groups(
    owned: dict[int, list[Event]], fresh: dict[str, lifecycle.Occurrence]
) -> list[_Group]:
    found: dict[tuple[str, str], _Group] = {}
    for events in owned.values():
        for event in events:
            occurrence = fresh.get(event.id)
            if occurrence is None:
                continue
            key = (occurrence.environment, occurrence.fingerprint_hash)
            if key not in found:
                found[key] = _Group(
                    digest=occurrence.fingerprint_hash,
                    environment=occurrence.environment,
                    fingerprint=list(occurrence.fingerprint),
                    members=[],
                )
            found[key].members.append((event, occurrence))
    for group in found.values():
        group.members.sort(key=lambda member: (member[1].timestamp, member[0].id))
    return [found[key] for key in sorted(found)]


def _owners(owned: dict[int, list[Event]]) -> dict[str, int]:
    return {
        event.id: issue_id for issue_id, events in owned.items() for event in events
    }


def _park_identities(holders: dict[int, Issue]) -> None:
    for issue_id in sorted(holders):
        parked = f"{TEMP_PREFIX}{issue_id}"
        Issue.objects.filter(pk=issue_id).update(fingerprint_hash=parked)
        holders[issue_id].fingerprint_hash = parked


def _settle_groups(
    report: EventRegroupReport,
    project: Project,
    groups: list[_Group],
    *,
    holders: dict[int, Issue],
    by_hash: dict[tuple[str, str], Issue],
    owner: dict[str, int],
) -> list[_Landing]:
    sources = _sources(groups, owner)
    landings: list[_Landing] = []
    claimed: set[int] = set()
    for group in groups:
        landing = _settle(
            report,
            project,
            group,
            holders=holders,
            by_hash=by_hash,
            sources=sources,
            owner=owner,
            claimed=claimed,
        )
        claimed.add(landing.issue.pk)
        landings.append(landing)
    return landings


def _sources(
    groups: list[_Group], owner: dict[str, int]
) -> dict[int, set[tuple[str, str]]]:
    found: dict[int, set[tuple[str, str]]] = {}
    for group in groups:
        for event, _ in group.members:
            found.setdefault(owner[event.id], set()).add(group.key)
    return found


def _settle(
    report: EventRegroupReport,
    project: Project,
    group: _Group,
    *,
    holders: dict[int, Issue],
    by_hash: dict[tuple[str, str], Issue],
    sources: dict[int, set[tuple[str, str]]],
    owner: dict[str, int],
    claimed: set[int],
) -> _Landing:
    holder = by_hash.get(group.key)
    if holder is not None and holder.pk not in claimed:
        return _Landing(group=group, issue=holder, changed=False)

    donor = holders[owner[group.members[-1][0].id]]
    if _one_to_one(group, sources, owner) and donor.pk not in claimed:
        report.issues_renamed += 1
        report.triage_migrated += 1
        return _Landing(group=group, issue=donor, changed=True)

    issue = _create_issue(report, project, group, donor)
    return _Landing(group=group, issue=issue, changed=True)


def _one_to_one(
    group: _Group,
    sources: dict[int, set[tuple[str, str]]],
    owner: dict[str, int],
) -> bool:
    contributors = {owner[event.id] for event, _ in group.members}
    if len(contributors) != 1:
        return False
    return sources[contributors.pop()] == {group.key}


def _create_issue(
    report: EventRegroupReport, project: Project, group: _Group, donor: Issue
) -> Issue:
    report.issues_created += 1
    issue = Issue(
        project=project,
        environment=group.environment,
        fingerprint_hash=group.digest,
        title=donor.title,
        level=donor.level,
    )
    issue.save()
    return issue


def _relink(
    report: EventRegroupReport,
    project: Project,
    store: EventStore,
    landings: list[_Landing],
    owner: dict[str, int],
) -> None:
    for landing in landings:
        target_id = landing.issue.pk
        moved = [
            event.id
            for event, _ in landing.group.members
            if owner[event.id] != target_id
        ]
        if not moved:
            continue
        report.events_moved += store.reassign_events(project.pk, moved, target_id)
        landing.changed = True
        for event_id in moved:
            owner[event_id] = target_id


def _final_rows(
    owned: dict[int, list[Event]],
    fresh: dict[str, lifecycle.Occurrence],
    owner: dict[str, int],
) -> dict[int, list[Row]]:
    final: dict[int, list[Row]] = {}
    for events in owned.values():
        for event in events:
            final.setdefault(owner[event.id], []).append((event, fresh.get(event.id)))
    return final


def _write_landings(landings: list[_Landing], final: dict[int, list[Row]]) -> None:
    for landing in landings:
        rows = final.get(landing.issue.pk, [])
        _rebuild_issue(landing.issue, landing.group, rows)
        if landing.changed:
            _log_regroup(landing.issue, landing.group)


def _write_holders(
    report: EventRegroupReport,
    holders: dict[int, Issue],
    landings: list[_Landing],
    parked: dict[int, str],
    final: dict[int, list[Row]],
) -> None:
    settled = {landing.issue.pk for landing in landings}
    for issue_id, issue in holders.items():
        if issue_id in settled:
            continue
        rows = final.get(issue_id, [])
        if not rows:
            report.orphans.append(issue.title)
            report.issues_deleted += 1
            issue.delete()
            continue
        issue.fingerprint_hash = parked[issue_id]
        _write_issue(issue, rows)


def _rebuild_issue(issue: Issue, group: _Group, rows: list[Row]) -> None:
    issue.fingerprint_hash = group.digest
    issue.fingerprint = list(group.fingerprint)
    issue.environment = group.environment
    for field_name, value in lifecycle.latest_fields(_newest(rows)).items():
        setattr(issue, field_name, value)
    _write_issue(issue, rows)


def _write_issue(issue: Issue, rows: list[Row]) -> None:
    issue.event_count = len(rows)
    issue.first_seen = min(event.timestamp for event, _ in rows)
    issue.last_seen = max(_seen_at(event, occurrence) for event, occurrence in rows)
    issue.save(
        update_fields=[
            "culprit",
            "environment",
            "event_count",
            "fingerprint",
            "fingerprint_hash",
            "first_seen",
            "last_seen",
            "level",
            "title",
        ]
    )
    aggregates.rebuild_from(issue, [(event.timestamp, event.tags) for event, _ in rows])


def _newest(rows: list[Row]) -> lifecycle.Occurrence:
    found = [occurrence for _, occurrence in rows if occurrence is not None]
    return max(found, key=lambda occurrence: occurrence.timestamp)


def _seen_at(event: Event, occurrence: lifecycle.Occurrence | None) -> datetime:
    if occurrence is None:
        return event.timestamp
    return occurrence.timestamp


def _log_regroup(issue: Issue, group: _Group) -> None:
    IssueActivity.objects.create(
        issue=issue,
        kind=ActivityKind.REGROUPED,
        at=timezone.now(),
        data={"fingerprint_hash": group.digest, "events": len(group.members)},
    )
