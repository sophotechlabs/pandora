from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from pandora.issues import merge
from pandora.people import audit


class Command(BaseCommand):
    help = "Fold issues that share a project and a fingerprint into one row"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="print what would be folded and change nothing",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["dry_run"]:
            report = merge.plan()
        else:
            report = merge.run()

        for line in report.lines():
            self.stdout.write(line)

        summary = (
            f"merge_issues: {len(report.groups)} fingerprint(s) with duplicates,"
            f" {report.issues_removed} issue(s) folded away"
        )
        if options["dry_run"]:
            self.stdout.write(f"{summary} (dry run, nothing written)")
            return

        audit.record(
            "",
            audit.CONFIG,
            "merge_issues",
            {"groups": len(report.groups), "removed": report.issues_removed},
            project_ids=[group.project_id for group in report.groups],
        )
        self.stdout.write(summary)
