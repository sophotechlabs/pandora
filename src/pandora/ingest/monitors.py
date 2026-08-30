from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.utils.text import slugify

from pandora.core.models import Project
from pandora.ingest.models import Monitor, MonitorStatus

IN_PROGRESS = "in_progress"
OK = "ok"
ERROR = "error"
STATUSES = (IN_PROGRESS, OK, ERROR)


@dataclass
class Sweep:
    missed: list[str]
    timed_out: list[str]

    def line(self) -> str:
        return (
            f"monitors: {len(self.missed)} missed, {len(self.timed_out)} over"
            " their runtime"
        )


def check_in(
    project: Project, slug: str, status: str, at: datetime, **fields: Any
) -> Monitor:
    """Take a check-in, creating the monitor from it if it is new.

    Upserting from the check-in itself is what removes the configuration step —
    a job that reports is a job that is watched, with no row to create first.
    """
    name = slugify(slug)[:100]
    monitor, _ = Monitor.objects.get_or_create(
        project=project,
        slug=name,
        defaults={"name": str(fields.get("name", "") or slug)[:200]},
    )
    for key in ("interval_minutes", "margin_minutes", "max_runtime_minutes"):
        value = fields.get(key)
        if value:
            setattr(monitor, key, int(value))
    environment = fields.get("environment")
    if environment:
        monitor.environment = str(environment)[:100]

    if status == IN_PROGRESS:
        monitor.status = MonitorStatus.IN_PROGRESS
        monitor.last_started = at
    elif status == ERROR:
        monitor.status = MonitorStatus.ERROR
        monitor.last_check_in = at
    else:
        monitor.status = MonitorStatus.OK
        monitor.last_check_in = at
    monitor.save()
    return monitor


def sweep(now: datetime) -> Sweep:
    missed = []
    timed_out = []
    for monitor in Monitor.objects.filter(active=True):
        if _running_too_long(monitor, now):
            monitor.status = MonitorStatus.TIMED_OUT
            monitor.save(update_fields=["status"])
            timed_out.append(monitor.slug)
            continue
        if _overdue(monitor, now):
            monitor.status = MonitorStatus.MISSED
            monitor.save(update_fields=["status"])
            missed.append(monitor.slug)
    return Sweep(missed=missed, timed_out=timed_out)


def _running_too_long(monitor: Monitor, now: datetime) -> bool:
    if monitor.status != MonitorStatus.IN_PROGRESS:
        return False
    if monitor.last_started is None:
        return False
    return now - monitor.last_started > monitor.runtime_limit


def _overdue(monitor: Monitor, now: datetime) -> bool:
    if monitor.status == MonitorStatus.MISSED:
        return False
    if monitor.last_check_in is None:
        return False
    return now - monitor.last_check_in > monitor.due_after
