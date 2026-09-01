from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from pandora.core.models import Project
from pandora.events.store import get_store
from pandora.events.types import Event
from pandora.people import audit
from pandora.scrub import service

BATCH = 500


@dataclass(frozen=True)
class RedactReport:
    scanned: int = 0
    rewritten: int = 0


def _scrubbed(event: Event, project: Project) -> Event | None:
    payload = service.scrub_payload(event.payload, project)
    extra = service.scrub_payload(event.extra, project)
    tags = service.scrub_payload(event.tags, project)
    message = service.scrub_message(event.message)
    unchanged = (
        payload == event.payload
        and extra == event.extra
        and tags == event.tags
        and message == event.message
    )
    if unchanged:
        return None
    return dataclasses.replace(
        event, payload=payload, extra=extra, tags=tags, message=message
    )


def redact(project: Project, batch: int = BATCH) -> RedactReport:
    store = get_store()
    scanned = 0
    rewritten = 0
    cursor = None
    while True:
        page = store.fetch(project.pk, before=cursor, limit=batch)
        if not page:
            break
        scanned += len(page)
        changed = [
            event for event in (_scrubbed(row, project) for row in page) if event
        ]
        if changed:
            rewritten += store.rewrite(project.pk, changed)
        cursor = page[-1].id
        if len(page) < batch:
            break
    return RedactReport(scanned=scanned, rewritten=rewritten)


class Command(BaseCommand):
    help = "Apply the current scrubbing rules to events already stored"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--project", default="", help="limit to one project slug")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="report what would change and roll back",
        )
        parser.add_argument("--batch", type=int, default=BATCH)

    def handle(self, *args: Any, **options: Any) -> None:
        if options["batch"] < 1:
            raise CommandError("--batch must be at least 1")
        projects = Project.objects.all()
        if options["project"]:
            projects = projects.filter(slug=options["project"])
            if not projects.exists():
                raise CommandError(f"no project with slug {options['project']!r}")

        report = self._run(list(projects), options["batch"], options["dry_run"])
        summary = f"redact: {report.scanned} scanned, {report.rewritten} rewritten"
        if options["dry_run"]:
            summary = f"{summary} (dry run, rolled back)"
        else:
            audit.record(
                "",
                audit.REDACT,
                options["project"],
                {"scanned": report.scanned, "rewritten": report.rewritten},
                project_ids=[project.pk for project in projects],
            )
        self.stdout.write(summary)

    def _run(self, projects: list[Project], batch: int, dry_run: bool) -> RedactReport:
        if not dry_run:
            return self._each(projects, batch)

        class Rollback(Exception):
            pass

        report = RedactReport()
        try:
            with transaction.atomic():
                report = self._each(projects, batch)
                raise Rollback
        except Rollback:
            pass
        return report

    def _each(self, projects: list[Project], batch: int) -> RedactReport:
        scanned = 0
        rewritten = 0
        for project in projects:
            result = redact(project, batch)
            scanned += result.scanned
            rewritten += result.rewritten
        return RedactReport(scanned=scanned, rewritten=rewritten)
