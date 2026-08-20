from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection as default_connection

from pandora.core import database

STAMP = "%Y%m%dT%H%M%SZ"


def snapshot_name(moment: datetime) -> str:
    return f"pandora-{moment.astimezone(UTC).strftime(STAMP)}.sqlite3"


class Command(BaseCommand):
    help = "Write a consistent snapshot of the SQLite database with VACUUM INTO"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--to",
            required=True,
            help=(
                "file the snapshot is written to, or a directory to name it in; "
                "an existing file is never overwritten"
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if default_connection.vendor != database.SQLITE_VENDOR:
            raise CommandError(
                "backup runs on SQLite only, this connection is "
                f"{default_connection.vendor}"
            )
        target = Path(options["to"])
        if target.is_dir():
            target = target / snapshot_name(datetime.now(UTC))
        if target.exists():
            raise CommandError(f"{target} already exists")
        if not target.parent.is_dir():
            raise CommandError(f"{target.parent} is not a directory")

        written = database.vacuum_into(default_connection, target)
        self.stdout.write(f"backup: wrote {written} bytes to {target}")
