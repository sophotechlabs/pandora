from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pandora.core.models import Project
from pandora.notify import digest


class Command(BaseCommand):
    help = "Summarise a period per project and queue it to the destinations"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--period",
            default="week",
            choices=sorted(digest.PERIODS),
            help="how far back to summarise (default: week)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="print the summary without queueing anything",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        period = options["period"]
        if period not in digest.PERIODS:
            raise CommandError(f"{period} is not a period")
        now = timezone.now()
        if options["dry_run"]:
            summaries = [
                digest.build(project, now, period)
                for project in Project.objects.all().order_by("slug")
            ]
        else:
            summaries = digest.send(now, period)
        for summary in summaries:
            for line in summary.lines():
                self.stdout.write(line)
