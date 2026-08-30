from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from django.db import connection, transaction
from django.db.models import Count
from django.utils import timezone

from pandora.events.types import EVENTS_TABLE
from pandora.issues import suggest
from pandora.issues.models import (
    ActivityKind,
    Episode,
    HourlyStat,
    Issue,
    IssueActivity,
    IssueAlias,
    IssueEnvironment,
    SilenceLink,
    TagStat,
    TriageState,
)
from pandora.notify.models import Delivery
from pandora.people.models import Assignment

TRIAGE_ORDER: dict[str, int] = {
    TriageState.RESOLVED: 0,
    TriageState.IGNORED: 1,
    TriageState.ACKNOWLEDGED: 2,
    TriageState.NEW: 3,
}


@dataclass
class Group:
    project_id: int
    fingerprint_hash: str
    keeper: int
    losers: list[int] = field(default_factory=list)
    environments: list[str] = field(default_factory=list)
    triage_state: str = ""


@dataclass
class Report:
    groups: list[Group] = field(default_factory=list)

    @property
    def issues_removed(self) -> int:
        return sum(len(group.losers) for group in self.groups)

    def lines(self) -> list[str]:
        return [
            f"{group.fingerprint_hash[:12]} in project {group.project_id}:"
            f" {len(group.losers) + 1} rows"
            f" ({', '.join(name or '<none>' for name in group.environments)})"
            f" -> {group.triage_state}"
            for group in self.groups
        ]


def duplicates() -> list[tuple[int, str]]:
    rows = (
        Issue.objects.values("project_id", "fingerprint_hash")
        .annotate(rows=Count("pk"))
        .filter(rows__gt=1)
        .order_by("project_id", "fingerprint_hash")
    )
    return [(row["project_id"], row["fingerprint_hash"]) for row in rows]


def plan() -> Report:
    report = Report()
    for project_id, fingerprint_hash in duplicates():
        issues = _group(project_id, fingerprint_hash)
        keeper = _keeper(issues)
        report.groups.append(
            Group(
                project_id=project_id,
                fingerprint_hash=fingerprint_hash,
                keeper=keeper.pk,
                losers=[issue.pk for issue in issues if issue.pk != keeper.pk],
                environments=sorted({issue.environment for issue in issues}),
                triage_state=_openest(issues),
            )
        )
    return report


def merge(keeper: Issue, others: Sequence[Issue], actor: str = "") -> int:
    """Fold issues a person decided are the same fault into one.

    The losing fingerprints become aliases, so the next occurrence of any of them
    lands on the keeper rather than minting the issue back. That is the half
    Sentry's merge leaves out, and it is what makes the merge worth making.
    """
    losers = [issue for issue in others if issue.pk != keeper.pk]
    if not losers:
        return 0
    aliases = [
        IssueAlias(
            project_id=keeper.project_id,
            fingerprint_hash=issue.fingerprint_hash,
            issue=keeper,
            title=issue.title,
            grouping_labels=dict(issue.grouping_labels or {})
            or suggest.labels_of(issue),
        )
        for issue in losers
    ]
    with transaction.atomic():
        _fold(
            Group(
                project_id=keeper.project_id,
                fingerprint_hash=keeper.fingerprint_hash,
                keeper=keeper.pk,
                losers=[issue.pk for issue in losers],
            ),
            issues=[keeper, *losers],
        )
        IssueAlias.objects.bulk_create(aliases, ignore_conflicts=True)
        IssueActivity.objects.create(
            issue=keeper,
            kind=ActivityKind.MERGED,
            actor=actor,
            at=timezone.now(),
            data={"fingerprints": [issue.fingerprint_hash for issue in losers]},
        )
    return len(losers)


def unmerge(keeper: Issue, fingerprint_hash: str, actor: str = "") -> bool:
    """Stop routing one folded fingerprint here.

    The history stays where the merge put it — the occurrences were counted into
    this issue and there is no honest way to take them back out once the events
    behind them have been pruned. What changes is the future: the next
    occurrence of that fingerprint opens its own issue again.
    """
    removed, _ = IssueAlias.objects.filter(
        issue=keeper, fingerprint_hash=fingerprint_hash
    ).delete()
    if not removed:
        return False
    IssueActivity.objects.create(
        issue=keeper,
        kind=ActivityKind.UNMERGED,
        actor=actor,
        at=timezone.now(),
        data={"fingerprint": fingerprint_hash},
    )
    return True


def resolve_alias(project_id: int, fingerprint_hash: str) -> Issue | None:
    alias = (
        IssueAlias.objects.filter(
            project_id=project_id, fingerprint_hash=fingerprint_hash
        )
        .select_related("issue")
        .first()
    )
    if alias is None:
        return None
    return alias.issue


def run() -> Report:
    report = plan()
    for group in report.groups:
        with transaction.atomic():
            _fold(group)
    return report


def _group(project_id: int, fingerprint_hash: str) -> list[Issue]:
    return list(
        Issue.objects.filter(
            project_id=project_id, fingerprint_hash=fingerprint_hash
        ).order_by("first_seen", "pk")
    )


def _keeper(issues: list[Issue]) -> Issue:
    return issues[0]


def _openest(issues: list[Issue]) -> str:
    ranked = sorted(issues, key=lambda issue: TRIAGE_ORDER.get(issue.triage_state, 0))
    return ranked[-1].triage_state


def _fold(group: Group, issues: Sequence[Issue] | None = None) -> None:
    if issues is None:
        issues = _group(group.project_id, group.fingerprint_hash)
    keeper = next(issue for issue in issues if issue.pk == group.keeper)
    losers = [issue for issue in issues if issue.pk != keeper.pk]
    if not losers:
        return
    loser_ids = [issue.pk for issue in losers]

    Episode.objects.filter(issue_id__in=loser_ids).update(issue_id=keeper.pk)
    IssueActivity.objects.filter(issue_id__in=loser_ids).update(issue_id=keeper.pk)
    SilenceLink.objects.filter(issue_id__in=loser_ids).update(issue_id=keeper.pk)
    _move_deliveries(loser_ids, keeper.pk)
    _drop_assignments(loser_ids)
    _fold_counters(HourlyStat, keeper.pk, loser_ids, ["hour"])
    _fold_counters(TagStat, keeper.pk, loser_ids, ["key", "value"])
    _fold_environments(keeper.pk, loser_ids)
    _move_events(loser_ids, keeper.pk)
    _write_keeper(keeper, list(issues))
    Issue.objects.filter(pk__in=loser_ids).delete()


def _move_deliveries(loser_ids: list[int], keeper_id: int) -> None:
    Delivery.objects.filter(issue_id__in=loser_ids).update(issue_id=keeper_id)


def _drop_assignments(loser_ids: list[int]) -> None:
    Assignment.objects.filter(issue_id__in=loser_ids).delete()


def _fold_counters(
    model, keeper_id: int, loser_ids: list[int], keys: list[str]
) -> None:
    for row in model.objects.filter(issue_id__in=loser_ids):
        match = {key: getattr(row, key) for key in keys}
        existing = model.objects.filter(issue_id=keeper_id, **match).first()
        if existing is None:
            row.issue_id = keeper_id
            row.save(update_fields=["issue"])
            continue
        existing.count = existing.count + row.count
        existing.save(update_fields=["count"])
        row.delete()


def _fold_environments(keeper_id: int, loser_ids: list[int]) -> None:
    for row in IssueEnvironment.objects.filter(issue_id__in=loser_ids):
        existing = IssueEnvironment.objects.filter(
            issue_id=keeper_id, name=row.name
        ).first()
        if existing is None:
            row.issue_id = keeper_id
            row.save(update_fields=["issue"])
            continue
        existing.first_seen = min(existing.first_seen, row.first_seen)
        existing.last_seen = max(existing.last_seen, row.last_seen)
        existing.event_count = existing.event_count + row.event_count
        existing.save(update_fields=["first_seen", "last_seen", "event_count"])
        row.delete()


def _move_events(loser_ids: list[int], keeper_id: int) -> None:
    if EVENTS_TABLE not in connection.introspection.table_names():
        return
    placeholders = ", ".join(["%s"] * len(loser_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {EVENTS_TABLE} SET issue_id = %s WHERE issue_id IN ({placeholders})",  # noqa: S608
            [keeper_id, *loser_ids],
        )


def _write_keeper(keeper: Issue, issues: list[Issue]) -> None:
    newest = max(issues, key=lambda issue: issue.last_seen)
    resolved = [issue.last_resolved_at for issue in issues if issue.last_resolved_at]
    keeper.first_seen = min(issue.first_seen for issue in issues)
    keeper.last_seen = newest.last_seen
    keeper.environment = newest.environment
    keeper.source_state = newest.source_state
    keeper.level = newest.level
    keeper.event_count = sum(issue.event_count for issue in issues)
    keeper.open_episode_count = sum(issue.open_episode_count for issue in issues)
    keeper.triage_state = _openest(issues)
    if resolved:
        keeper.last_resolved_at = max(resolved)
    if any(issue.snoozed_until is None for issue in issues):
        keeper.snoozed_until = None
    if any(issue.snoozed_past_count is None for issue in issues):
        keeper.snoozed_past_count = None
    keeper.save()
