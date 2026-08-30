from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db.models import F, Sum
from django.utils.dateparse import parse_datetime

from pandora.core.models import Project
from pandora.releases.models import SessionBucket
from pandora.releases.versions import is_parsed, sort_key

EXITED = "exited"
CRASHED = "crashed"
ABNORMAL = "abnormal"
ERRORED = "errored"
HEALTHY = "healthy"
ADOPTION_WINDOW = timedelta(hours=24)
MAX_ITEMS = 100


@dataclass(frozen=True)
class Health:
    sessions: int
    crashed: int
    errored: int
    abnormal: int

    @property
    def crash_free(self) -> float:
        if not self.sessions:
            return 1.0
        return 1 - (self.crashed / self.sessions)

    @property
    def crash_free_percent(self) -> float:
        return round(self.crash_free * 100, 3)

    @property
    def healthy(self) -> int:
        return max(0, self.sessions - self.crashed - self.errored - self.abnormal)


def accept(project: Project, payload: Any, received_at: datetime) -> int:
    """Take one session, or a pre-aggregated bucket of them.

    Sessions bypass the gate and sampling by design and are not billed by
    anyone, which is why crash counts and crashed-session counts legitimately
    disagree. They land in their own aggregated table rather than the event
    store, because their shape is a counter, not a record.
    """
    if not isinstance(payload, Mapping):
        return 0
    if "aggregates" in payload:
        return _aggregated(project, payload, received_at)
    return _single(project, payload, received_at)


def health(
    project: Project, version: str, environment: str = "", since: datetime | None = None
) -> Health:
    rows = SessionBucket.objects.filter(project=project, version=version)
    if environment:
        rows = rows.filter(environment=environment)
    if since is not None:
        rows = rows.filter(hour__gte=since)
    totals = rows.aggregate(
        sessions=Sum("sessions"),
        crashed=Sum("crashed"),
        errored=Sum("errored"),
        abnormal=Sum("abnormal"),
    )
    return Health(
        sessions=totals["sessions"] or 0,
        crashed=totals["crashed"] or 0,
        errored=totals["errored"] or 0,
        abnormal=totals["abnormal"] or 0,
    )


def adoption(project: Project, version: str, now: datetime) -> float:
    since = now - ADOPTION_WINDOW
    mine = health(project, version, since=since).sessions
    everything = (
        SessionBucket.objects.filter(project=project, hour__gte=since).aggregate(
            total=Sum("sessions")
        )["total"]
        or 0
    )
    if not everything:
        return 0.0
    return mine / everything


def _single(project: Project, payload: Mapping[str, Any], received_at: datetime) -> int:
    attrs = payload.get("attrs") or {}
    started = _moment(payload.get("started")) or received_at
    status = str(payload.get("status", EXITED))
    errors = int(payload.get("errors", 0) or 0)
    _bump(
        project,
        str(attrs.get("release", "")),
        str(attrs.get("environment", "")),
        started,
        sessions=1,
        crashed=int(status == CRASHED),
        abnormal=int(status == ABNORMAL),
        errored=int(status not in (CRASHED, ABNORMAL) and errors > 0),
    )
    return 1


def _aggregated(
    project: Project, payload: Mapping[str, Any], received_at: datetime
) -> int:
    attrs = payload.get("attrs") or {}
    release = str(attrs.get("release", ""))
    environment = str(attrs.get("environment", ""))
    buckets = payload.get("aggregates") or []
    if not isinstance(buckets, list):
        return 0
    taken = 0
    for bucket in buckets[:MAX_ITEMS]:
        if not isinstance(bucket, Mapping):
            continue
        started = _moment(bucket.get("started")) or received_at
        exited = int(bucket.get("exited", 0) or 0)
        crashed = int(bucket.get("crashed", 0) or 0)
        abnormal = int(bucket.get("abnormal", 0) or 0)
        errored = int(bucket.get("errored", 0) or 0)
        total = exited + crashed + abnormal + errored
        if not total:
            continue
        _bump(
            project,
            release,
            environment,
            started,
            sessions=total,
            crashed=crashed,
            abnormal=abnormal,
            errored=errored,
        )
        taken += total
    return taken


def _bump(
    project: Project,
    version: str,
    environment: str,
    started: datetime,
    *,
    sessions: int,
    crashed: int,
    abnormal: int,
    errored: int,
) -> None:
    hour = started.replace(minute=0, second=0, microsecond=0)
    updated = SessionBucket.objects.filter(
        project=project, version=version, environment=environment, hour=hour
    ).update(
        sessions=F("sessions") + sessions,
        crashed=F("crashed") + crashed,
        abnormal=F("abnormal") + abnormal,
        errored=F("errored") + errored,
    )
    if updated:
        return
    SessionBucket.objects.create(
        project=project,
        version=version,
        environment=environment,
        hour=hour,
        sort_key=sort_key(version),
        parsed=is_parsed(version),
        sessions=sessions,
        crashed=crashed,
        abnormal=abnormal,
        errored=errored,
    )


def _moment(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return parse_datetime(str(value))
