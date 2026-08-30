from __future__ import annotations

import time
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from pandora.ingest import monitors


class Command(BaseCommand):
    help = "Mark the scheduled jobs that did not check in"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--loop",
            type=int,
            default=0,
            metavar="SECONDS",
            help="keep running, one sweep every SECONDS (default: one sweep)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        interval = options["loop"]
        if interval <= 0:
            self._sweep()
            return
        while True:
            self._sweep()
            time.sleep(interval)

    def _sweep(self) -> None:
        report = monitors.sweep(timezone.now())
        for slug in report.missed:
            self.stdout.write(f"monitors: {slug} missed its window")
        for slug in report.timed_out:
            self.stdout.write(f"monitors: {slug} is over its runtime")
        self.stdout.write(report.line())
