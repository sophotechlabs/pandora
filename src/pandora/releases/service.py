from __future__ import annotations

from datetime import datetime, timedelta

from django.db.models import F, Max, Min, Value
from django.db.models.functions import Greatest, Least

from pandora.core.models import Project
from pandora.issues.models import Issue, TriageState
from pandora.issues.triage import OPEN_STATES
from pandora.releases.models import (
    Deploy,
    DeployState,
    Release,
    ReleaseEnvironment,
    Resolution,
)
from pandora.releases.versions import is_parsed, sort_key

DEPLOY_TIMEOUT = timedelta(minutes=60)


def record(
    project: Project,
    version: str,
    dist: str,
    environment: str,
    at: datetime,
) -> Release | None:
    """A process is on a release the moment it sends an event tagged with it.

    That is the whole rollout signal, and it is better than a marker posted by
    CI: with more than one replica the marker says the deploy finished while
    half the pods are still on the old image.
    """
    name = version.strip()
    if not name:
        return None
    release, created = Release.objects.get_or_create(
        project=project,
        version=name[:250],
        dist=dist.strip()[:100],
        defaults={
            "sort_key": sort_key(name),
            "parsed": is_parsed(name),
            "first_seen": at,
            "last_seen": at,
            "event_count": 1,
        },
    )
    if not created:
        Release.objects.filter(pk=release.pk).update(
            event_count=F("event_count") + 1,
            first_seen=Least(F("first_seen"), Value(at)),
            last_seen=Greatest(F("last_seen"), Value(at)),
        )
    _record_environment(release, environment, at)
    return release


def _record_environment(release: Release, environment: str, at: datetime) -> None:
    rollout, created = ReleaseEnvironment.objects.get_or_create(
        release=release,
        name=environment,
        defaults={"first_seen": at, "last_seen": at, "event_count": 1},
    )
    if created:
        return
    ReleaseEnvironment.objects.filter(pk=rollout.pk).update(
        first_seen=Least(F("first_seen"), Value(at)),
        last_seen=Greatest(F("last_seen"), Value(at)),
        event_count=F("event_count") + 1,
    )


def rollout(release: Release) -> list[ReleaseEnvironment]:
    return list(release.environments.all())


def previous(release: Release, environment: str = "") -> Release | None:
    rows = Release.objects.filter(
        project_id=release.project_id, sort_key__lt=release.sort_key
    )
    if environment:
        rows = rows.filter(environments__name=environment)
    return rows.order_by("-sort_key", "-first_seen").first()


def suspect_deploy(issue: Issue) -> Deploy | None:
    """The last deploy before the issue was first seen.

    A dozen lines of SQL against the question people actually ask, and it needs
    no repository access — suspect *commit* does, and is a later unit.
    """
    return (
        Deploy.objects.filter(
            release__project_id=issue.project_id, started_at__lte=issue.first_seen
        )
        .order_by("-started_at")
        .select_related("release")
        .first()
    )


def resolve_in(
    issue: Issue,
    *,
    release: Release | None = None,
    in_next: bool = False,
    actor: str = "",
    at: datetime,
) -> Resolution:
    boundary = ""
    if release is not None:
        boundary = release.sort_key
    resolution, _ = Resolution.objects.update_or_create(
        issue=issue,
        defaults={
            "release": release,
            "sort_key": boundary,
            "in_next": in_next,
            "actor": actor,
            "at": at,
        },
    )
    return resolution


def regressed(issue: Issue, version: str) -> bool:
    """Whether an event on `version` reopens a release-resolved issue.

    Countly's reoccurred semantics: an equal or lower version leaves it
    resolved, a higher one does not. Nobody free implements it, and it is what
    makes *resolved in the next release* mean anything.
    """
    resolution = Resolution.objects.filter(issue=issue).first()
    if resolution is None:
        return True
    if not version.strip():
        return True
    arriving = sort_key(version)
    if resolution.in_next:
        return arriving > resolution.sort_key
    if not resolution.sort_key:
        return True
    return arriving > resolution.sort_key


def latest(project: Project, environment: str = "") -> Release | None:
    rows = Release.objects.filter(project=project)
    if environment:
        rows = rows.filter(environments__name=environment)
    return rows.order_by("-sort_key", "-first_seen").first()


def stalled(project: Project, now: datetime) -> list[Deploy]:
    cutoff = now - DEPLOY_TIMEOUT
    return list(
        Deploy.objects.filter(
            release__project=project,
            state=DeployState.STARTED,
            started_at__lt=cutoff,
        ).select_related("release")
    )


def time_out(now: datetime) -> int:
    cutoff = now - DEPLOY_TIMEOUT
    return Deploy.objects.filter(
        state=DeployState.STARTED, started_at__lt=cutoff
    ).update(state=DeployState.TIMED_OUT, finished_at=now)


def resolve_on_deploy(
    project: Project, release: Release, environment: str, now: datetime
) -> int:
    """Wipe the board on deploy, and let what comes back come back.

    Honeybadger does this by default and Airbrake does it per environment. It is
    the opinionated option and it produces triage discipline nothing else does,
    so it is off until a project asks for it.
    """
    if not project.resolve_on_deploy:
        return 0
    open_issues = Issue.objects.filter(project=project, triage_state__in=OPEN_STATES)
    if environment:
        open_issues = open_issues.filter(environments__name=environment).distinct()
    resolved = 0
    for issue in open_issues:
        resolve_in(issue, release=release, actor="deploy", at=now)
        resolved += 1
    Issue.objects.filter(pk__in=[issue.pk for issue in open_issues]).update(
        triage_state=TriageState.RESOLVED, last_resolved_at=now
    )
    return resolved


def window(release: Release) -> tuple[datetime | None, datetime | None]:
    row = release.environments.aggregate(
        opened=Min("first_seen"), closed=Max("last_seen")
    )
    return (row["opened"], row["closed"])
