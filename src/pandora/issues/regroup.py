from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from pandora.core.models import Project
from pandora.events.store import EventStore, get_store
from pandora.issues import aggregates, grouping
from pandora.issues.models import (
    ActivityKind,
    Episode,
    Issue,
    IssueActivity,
    SourceState,
)

TEMP_PREFIX = "regroup-"


@dataclass
class RegroupReport:
    projects: int = 0
    episodes: int = 0
    issues_before: int = 0
    issues_after: int = 0
    episodes_moved: int = 0
    events_moved: int = 0
    issues_created: int = 0
    issues_renamed: int = 0
    issues_deleted: int = 0
    triage_migrated: int = 0
    orphans: list[str] = field(default_factory=list)


@dataclass
class _Group:
    digest: str
    fingerprint: list[str]
    grouping_labels: dict[str, str]
    episodes: list[Episode]


class _Rollback(Exception):
    pass


def regroup(
    project: Project | None = None,
    dry_run: bool = False,
    store: EventStore | None = None,
) -> RegroupReport:
    report = RegroupReport()
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


def _run(report: RegroupReport, project: Project | None, store: EventStore) -> None:
    projects = Project.objects.all()
    if project is not None:
        projects = Project.objects.filter(pk=project.pk)
    for row in projects.order_by("pk"):
        _regroup_project(report, row, store)


def _regroup_project(
    report: RegroupReport, project: Project, store: EventStore
) -> None:
    episodes = list(Episode.objects.filter(project=project).order_by("starts_at", "pk"))
    report.projects += 1
    report.episodes += len(episodes)
    if not episodes:
        return

    groups = _groups(project, episodes)
    before_ids = {episode.issue_id for episode in episodes}
    report.issues_before += len(before_ids)

    owned: dict[int, set[str]] = {}
    for group in groups:
        for episode in group.episodes:
            owned.setdefault(episode.issue_id, set()).add(group.digest)

    donors = {issue.pk: issue for issue in Issue.objects.filter(pk__in=before_ids)}
    by_hash = {issue.fingerprint_hash: issue for issue in donors.values()}
    _park_identities(donors)

    claimed: set[int] = set()
    for group in groups:
        issue, changed = _settle(
            report,
            project,
            group,
            donors=donors,
            by_hash=by_hash,
            owned=owned,
            claimed=claimed,
            store=store,
        )
        claimed.add(issue.pk)
        _rebuild_issue(issue, group)
        if changed:
            _log_regroup(issue, group)

    report.issues_after += len(claimed)
    _drop_orphans(report, before_ids - claimed)


def _park_identities(donors: dict[int, Issue]) -> None:
    for issue_id in sorted(donors):
        parked = f"{TEMP_PREFIX}{issue_id}"
        Issue.objects.filter(pk=issue_id).update(fingerprint_hash=parked)
        donors[issue_id].fingerprint_hash = parked


def _groups(project: Project, episodes: list[Episode]) -> list[_Group]:
    rules = grouping.load_rules(project)
    found: dict[str, _Group] = {}
    for episode in episodes:
        alertname = episode.labels.get(grouping.ALERTNAME, "")
        rule = grouping.match_rule(alertname, rules)
        fingerprint = grouping.compute_fingerprint(rule, episode.labels)
        digest = grouping.fingerprint_hash(fingerprint)
        if digest not in found:
            found[digest] = _Group(
                digest=digest,
                fingerprint=fingerprint,
                grouping_labels=grouping.surviving_labels(rule, episode.labels),
                episodes=[],
            )
        found[digest].episodes.append(episode)
    return [found[digest] for digest in sorted(found)]


def _settle(
    report: RegroupReport,
    project: Project,
    group: _Group,
    *,
    donors: dict[int, Issue],
    by_hash: dict[str, Issue],
    owned: dict[int, set[str]],
    claimed: set[int],
    store: EventStore,
) -> tuple[Issue, bool]:
    donor = donors[group.episodes[-1].issue_id]
    sources = {episode.issue_id for episode in group.episodes}
    one_to_one = len(sources) == 1 and owned[donor.pk] == {group.digest}
    holder = by_hash.get(group.digest)

    changed = False
    if holder is not None and holder.pk not in claimed:
        target = holder
    elif one_to_one and donor.pk not in claimed:
        target = donor
        report.issues_renamed += 1
        report.triage_migrated += 1
        changed = True
    else:
        target = _create_issue(report, project, group, donor)
        changed = True

    moved = [episode for episode in group.episodes if episode.issue_id != target.pk]
    if moved:
        Episode.objects.filter(pk__in=[episode.pk for episode in moved]).update(
            issue=target
        )
        report.events_moved += store.reassign(
            project.pk, [str(episode.pk) for episode in moved], target.pk
        )
        report.episodes_moved += len(moved)
        changed = True
        for episode in moved:
            episode.issue_id = target.pk
    return target, changed


def _create_issue(
    report: RegroupReport, project: Project, group: _Group, donor: Issue
) -> Issue:
    report.issues_created += 1
    issue = Issue(
        project=project,
        fingerprint_hash=group.digest,
        title=donor.title,
        level=donor.level,
    )
    issue.save()
    return issue


def _rebuild_issue(issue: Issue, group: _Group) -> None:
    open_count = sum(1 for episode in group.episodes if episode.ends_at is None)
    issue.fingerprint_hash = group.digest
    issue.fingerprint = group.fingerprint
    issue.grouping_labels = group.grouping_labels
    issue.culprit = grouping.derive_culprit(group.grouping_labels)
    issue.environment = group.episodes[-1].environment
    issue.event_count = len(group.episodes)
    issue.open_episode_count = open_count
    issue.first_seen = min(episode.starts_at for episode in group.episodes)
    issue.last_seen = max(episode.last_delivery_at for episode in group.episodes)
    if open_count > 0:
        issue.source_state = SourceState.FIRING
    else:
        issue.source_state = SourceState.RESOLVED
    issue.save(
        update_fields=[
            "culprit",
            "environment",
            "event_count",
            "fingerprint",
            "fingerprint_hash",
            "first_seen",
            "grouping_labels",
            "last_seen",
            "open_episode_count",
            "source_state",
        ]
    )
    aggregates.rebuild(issue, group.episodes)


def _log_regroup(issue: Issue, group: _Group) -> None:
    IssueActivity.objects.create(
        issue=issue,
        kind=ActivityKind.REGROUPED,
        at=timezone.now(),
        data={"fingerprint_hash": group.digest, "episodes": len(group.episodes)},
    )


def _drop_orphans(report: RegroupReport, orphan_ids: set[int]) -> None:
    for issue in Issue.objects.filter(pk__in=orphan_ids).order_by("pk"):
        report.orphans.append(issue.title)
        report.issues_deleted += 1
        issue.delete()
