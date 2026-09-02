from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from pandora.core.models import Project
from pandora.events import archive
from pandora.people import audit


class Command(BaseCommand):
    help = "Write stored events to gzipped JSON Lines, hive-partitioned by hour"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--project", default="", help="project slug, or all")
        parser.add_argument("--since", default="", help="ISO 8601, default 24h ago")
        parser.add_argument("--until", default="", help="ISO 8601, default now")
        parser.add_argument(
            "--to",
            default="",
            help="directory to write into (defaults to PANDORA_ARCHIVE_DIR)",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="skip hourly files already present at the destination",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        since = _moment(options["since"], now - timedelta(hours=24))
        until = _moment(options["until"], now)
        if since >= until:
            raise CommandError("--since must be before --until")
        destination = None
        if options["to"]:
            destination = Path(options["to"])
        if destination is None and archive.root() is None:
            raise CommandError("pass --to or set PANDORA_ARCHIVE_DIR")

        projects = Project.objects.all()
        if options["project"]:
            projects = projects.filter(slug=options["project"])
            if not projects.exists():
                raise CommandError(f"no project called {options['project']}")

        written = 0
        events = 0
        skipped = 0
        project_ids = []
        exporter = archive.export
        if options["resume"]:
            exporter = archive.resume
        for project in projects:
            project_ids.append(project.pk)
            report = exporter(
                project.pk,
                since,
                until,
                destination=destination,
            )
            for line in report.lines():
                self.stdout.write(line)
            written += len(report.files)
            events += report.events
            skipped += len(report.skipped)

        audit.record(
            "",
            audit.ARCHIVE,
            f"{since.isoformat()}..{until.isoformat()}",
            {"files": written, "events": events, "skipped": skipped},
            project_ids=project_ids,
        )
        self.stdout.write(
            f"archive: {written} file(s), {events} event(s), {skipped} skipped"
        )


def _moment(raw: str, fallback: Any) -> Any:
    if not raw:
        return fallback
    parsed = parse_datetime(raw)
    if parsed is None:
        raise CommandError(f"{raw!r} is not an ISO 8601 timestamp")
    return parsed
