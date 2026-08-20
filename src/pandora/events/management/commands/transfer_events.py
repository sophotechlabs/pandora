from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import dj_database_url
from django.core.management.base import BaseCommand, CommandError
from django.db import connection as default_connection
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.utils import DEFAULT_DB_ALIAS, ConnectionHandler

from pandora.core.models import Project
from pandora.events import transfer as events_transfer
from pandora.events.store import get_store


class Command(BaseCommand):
    help = "Copy stored events out of another pandora database into this one"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--from",
            dest="source",
            required=True,
            help="database url the events are read from",
        )
        parser.add_argument(
            "--batch",
            type=int,
            default=events_transfer.BATCH,
            help="events read per page",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        batch = int(options["batch"])
        if batch < 1:
            raise CommandError("--batch must be at least 1")

        project_ids = list(Project.objects.order_by("pk").values_list("pk", flat=True))
        if not project_ids:
            raise CommandError(
                "this database holds no projects — restore the dump before "
                "transferring events"
            )

        with source_database(options["source"]) as source_connection:
            source = get_store(source_connection)
            target = get_store(default_connection)
            report = events_transfer.transfer(
                source,
                target,
                project_ids,
                batch=batch,
            )

        self.stdout.write(
            f"transfer_events: copied {report.events} events "
            f"across {report.projects} projects"
        )


@contextmanager
def source_database(url: str) -> Iterator[BaseDatabaseWrapper]:
    handler = ConnectionHandler({DEFAULT_DB_ALIAS: _settings(url)})
    connection = handler[DEFAULT_DB_ALIAS]
    try:
        yield connection
    finally:
        connection.close()


def _settings(url: str) -> Any:
    try:
        config = dj_database_url.parse(url)
    except Exception as error:
        raise CommandError(
            f"could not read {url!r} as a database url: {error}"
        ) from error
    return config
