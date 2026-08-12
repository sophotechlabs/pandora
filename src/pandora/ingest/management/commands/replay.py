from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from pandora.ingest.replay import (
    DEFAULT_LIMIT,
    STATE_SETS,
    ReplayResult,
    pending_envelopes,
    replay,
)

__all__ = [
    "DEFAULT_LIMIT",
    "STATE_SETS",
    "Command",
    "ReplayResult",
    "pending_envelopes",
    "replay",
]


class Command(BaseCommand):
    help = "Re-run the consumer over envelopes that never reached done"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--state",
            choices=sorted(STATE_SETS),
            default="all",
            help="which envelopes to pick up (default: all)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_LIMIT,
            help=f"how many to attempt in one run (default: {DEFAULT_LIMIT})",
        )
        parser.add_argument(
            "--project",
            default="",
            help="restrict to one project slug",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="report what would be replayed and change nothing",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        states = STATE_SETS[options["state"]]
        limit = options["limit"]
        project_slug = options["project"].strip()

        if options["dry_run"]:
            waiting = pending_envelopes(states, limit, project_slug)
            self.stdout.write(f"replay: {len(waiting)} envelope(s) would be replayed")
            for envelope in waiting:
                self.stdout.write(f"  {envelope.pk} {envelope.source} {envelope.state}")
            return

        result = replay(states, limit, project_slug)
        self.stdout.write(
            f"replay: {result.attempted} attempted, "
            f"{result.done} done, {result.failed} still failing"
        )
