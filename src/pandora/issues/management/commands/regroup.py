from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from pandora.core.models import Project
from pandora.issues import regroup as regroup_issues

ORPHAN_SAMPLE = 20


class Command(BaseCommand):
    help = "Recompute issue grouping from the permanent episode history"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--project",
            default="",
            help="limit the rebuild to one project slug",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="report what would change without writing",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        project = None
        slug = options["project"].strip()
        if slug:
            project = Project.objects.filter(slug=slug).first()
            if project is None:
                raise CommandError(f"no project with slug {slug!r}")

        dry_run = bool(options["dry_run"])
        report = regroup_issues.regroup(project=project, dry_run=dry_run)

        if dry_run:
            verb = "would rebuild"
        else:
            verb = "rebuilt"
        self.stdout.write(
            f"regroup: {verb} {report.issues_before} issues into "
            f"{report.issues_after} from {report.episodes} episodes "
            f"across {report.projects} projects"
        )
        self.stdout.write(
            f"regroup: {report.issues_created} created, "
            f"{report.issues_renamed} regrouped in place, "
            f"{report.episodes_moved} episodes moved, "
            f"{report.triage_migrated} triage states carried, "
            f"{report.issues_deleted} emptied issues removed"
        )
        for title in report.orphans[:ORPHAN_SAMPLE]:
            self.stdout.write(f"regroup: orphaned {title}")
