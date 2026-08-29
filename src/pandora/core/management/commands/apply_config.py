from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from pandora.core import config


class Command(BaseCommand):
    help = "Reconcile projects, tokens, keys, grouping rules and links from a YAML file"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--path",
            default="",
            help="config file to apply (defaults to PANDORA_CONFIG)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="print what would change and roll back",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        path = options["path"] or settings.PANDORA_CONFIG
        if not path:
            raise CommandError("pass --path or set PANDORA_CONFIG")

        try:
            document = config.load(path)
        except OSError as error:
            raise CommandError(f"cannot read {path}: {error}") from error
        except config.ConfigError as error:
            raise CommandError(str(error)) from error

        try:
            report = self._apply(document, options["dry_run"])
        except config.ConfigError as error:
            raise CommandError(str(error)) from error

        for line in report.lines():
            self.stdout.write(line)
        summary = (
            f"apply_config: {len(report.created)} created, "
            f"{len(report.updated)} updated, "
            f"{len(report.deactivated)} deactivated, "
            f"{len(report.unchanged)} unchanged"
        )
        if options["dry_run"]:
            summary = f"{summary} (dry run, rolled back)"
        self.stdout.write(summary)

    def _apply(self, document: Any, dry_run: bool) -> config.Report:
        if not dry_run:
            with transaction.atomic():
                return config.apply(document)

        class Rollback(Exception):
            pass

        report = config.Report()
        try:
            with transaction.atomic():
                report = config.apply(document)
                raise Rollback
        except Rollback:
            pass
        return report
