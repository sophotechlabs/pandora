from __future__ import annotations

from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
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
TERMINAL_DEPLOY_STATES = (
    DeployState.SUCCEEDED,
    DeployState.FAILED,
    DeployState.TIMED_OUT,
)


class DeployConflict(ValueError):
    pass


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


def ensure_release(
    project: Project,
    version: str,
    dist: str,
    at: datetime,
) -> Release:
    name = version.strip()
    if not name:
        raise DeployConflict("release must not be empty")
    if len(name) > 250:
        raise DeployConflict("release is too long")
    variant = dist.strip()
    if len(variant) > 100:
        raise DeployConflict("release dist is too long")
    release, _ = Release.objects.get_or_create(
        project=project,
        version=name,
        dist=variant,
        defaults={
            "sort_key": sort_key(name),
            "parsed": is_parsed(name),
            "first_seen": at,
            "last_seen": at,
        },
    )
    return release


def transition_deploy(
    project: Project,
    release: Release,
    *,
    identifier: str,
    environment: str,
    state: DeployState | str,
    at: datetime,
    name: str = "",
    url: str = "",
) -> tuple[Deploy, bool]:
    _validate_release_project(project, release)
    key = identifier.strip()
    if not key:
        raise DeployConflict("deploy identifier must not be empty")
    if len(key) > 128:
        raise DeployConflict("deploy identifier is too long")
    environment_value = environment.strip()
    if len(environment_value) > 100:
        raise DeployConflict("deploy environment is too long")
    name_value = name.strip()
    if len(name_value) > 200:
        raise DeployConflict("deploy name is too long")
    url_value = url.strip()
    if len(url_value) > 500:
        raise DeployConflict("deploy URL is too long")
    state_value = str(state)
    if state_value not in DeployState.values:
        raise DeployConflict(f"unknown deploy state {state_value!r}")
    try:
        with transaction.atomic():
            return _transition_deploy(
                project,
                release,
                identifier=key,
                environment=environment_value,
                state=state_value,
                at=at,
                name=name_value,
                url=url_value,
            )
    except IntegrityError:
        with transaction.atomic():
            return _transition_deploy(
                project,
                release,
                identifier=key,
                environment=environment_value,
                state=state_value,
                at=at,
                name=name_value,
                url=url_value,
            )


def _transition_deploy(
    project: Project,
    release: Release,
    *,
    identifier: str,
    environment: str,
    state: str,
    at: datetime,
    name: str,
    url: str,
) -> tuple[Deploy, bool]:
    deploy = (
        Deploy.objects.select_for_update()
        .filter(project=project, identifier=identifier)
        .first()
    )
    if deploy is None:
        if state != DeployState.STARTED:
            raise DeployConflict("deploy must be started before it can finish")
        return (
            Deploy.objects.create(
                project=project,
                release=release,
                identifier=identifier,
                environment=environment,
                state=DeployState.STARTED,
                started_at=at,
                name=name,
                url=url,
            ),
            True,
        )
    _validate_deploy_context(deploy, release, environment, name, url)
    if state == DeployState.STARTED:
        if deploy.state == DeployState.STARTED:
            return deploy, False
        raise DeployConflict(f"deploy is already {deploy.state}")
    if at < deploy.started_at:
        raise DeployConflict("deploy finish is before its start")
    if deploy.state == state:
        return deploy, False
    allowed = deploy.state == DeployState.STARTED
    late_finish = deploy.state == DeployState.TIMED_OUT and state in (
        DeployState.SUCCEEDED,
        DeployState.FAILED,
    )
    if not allowed and not late_finish:
        raise DeployConflict(f"deploy is already {deploy.state}")
    deploy.state = state
    deploy.finished_at = at
    deploy.save(update_fields=["state", "finished_at"])
    return deploy, True


def _validate_deploy_context(
    deploy: Deploy,
    release: Release,
    environment: str,
    name: str,
    url: str,
) -> None:
    if deploy.release_id != release.pk:
        raise DeployConflict("deploy identifier belongs to another release")
    if deploy.environment != environment:
        raise DeployConflict("deploy identifier belongs to another environment")
    if name and deploy.name != name:
        raise DeployConflict("deploy identifier has a different name")
    if url and deploy.url != url:
        raise DeployConflict("deploy identifier has a different URL")


def record_completed_deploy(
    project: Project,
    release: Release,
    *,
    identifier: str,
    environment: str,
    started_at: datetime,
    finished_at: datetime,
    name: str = "",
    url: str = "",
) -> tuple[Deploy, bool]:
    _validate_release_project(project, release)
    key = identifier.strip()
    if not key:
        raise DeployConflict("deploy identifier must not be empty")
    if len(key) > 128:
        raise DeployConflict("deploy identifier is too long")
    environment_value = environment.strip()
    if len(environment_value) > 100:
        raise DeployConflict("deploy environment is too long")
    name_value = name.strip()
    if len(name_value) > 200:
        raise DeployConflict("deploy name is too long")
    url_value = url.strip()
    if len(url_value) > 500:
        raise DeployConflict("deploy URL is too long")
    if finished_at < started_at:
        raise DeployConflict("deploy finish is before its start")
    try:
        with transaction.atomic():
            deploy, created = Deploy.objects.get_or_create(
                project=project,
                identifier=key,
                defaults={
                    "release": release,
                    "environment": environment_value,
                    "state": DeployState.SUCCEEDED,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "name": name_value,
                    "url": url_value,
                },
            )
    except IntegrityError:
        deploy = Deploy.objects.get(project=project, identifier=key)
        created = False
    if not created:
        _validate_completed_deploy(
            deploy,
            release,
            environment_value,
            name_value,
            url_value,
        )
    return deploy, created


def _validate_completed_deploy(
    deploy: Deploy,
    release: Release,
    environment: str,
    name: str,
    url: str,
) -> None:
    _validate_deploy_context(deploy, release, environment, name, url)
    if deploy.name != name:
        raise DeployConflict("deploy identifier has a different name")
    if deploy.url != url:
        raise DeployConflict("deploy identifier has a different URL")
    if deploy.state != DeployState.SUCCEEDED:
        raise DeployConflict(f"deploy is already {deploy.state}")


def _validate_release_project(project: Project, release: Release) -> None:
    if release.project_id != project.pk:
        raise DeployConflict("release belongs to another project")


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
            project_id=issue.project_id,
            started_at__lte=issue.first_seen,
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
    return stalled_for([project.pk], now)


def stalled_for(project_ids: list[int] | None, now: datetime) -> list[Deploy]:
    cutoff = now - DEPLOY_TIMEOUT
    rows = Deploy.objects.filter(
        state__in=(DeployState.STARTED, DeployState.TIMED_OUT),
        started_at__lt=cutoff,
    )
    if project_ids is not None:
        rows = rows.filter(project_id__in=project_ids)
    return list(rows.select_related("project", "release").order_by("started_at"))


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
