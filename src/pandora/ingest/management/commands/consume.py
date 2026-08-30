from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from django import db
from django.core.management.base import BaseCommand
from django.utils import timezone

from pandora.ingest import consumer

STALE_AFTER = timedelta(minutes=15)
log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Drain the envelope inbox, the durable queue ingest already writes to"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--loop",
            type=int,
            default=0,
            metavar="SECONDS",
            help="keep running, one pass every SECONDS (default: one pass)",
        )
        parser.add_argument(
            "--batch",
            type=int,
            default=consumer.BATCH,
            help=f"envelopes to claim per pass (default: {consumer.BATCH})",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        interval = options["loop"]
        batch = options["batch"]
        if interval <= 0:
            self._pass(batch)
            return

        while True:
            self._pass(batch)
            time.sleep(interval)

    def _pass(self, batch: int) -> None:
        reclaimed = consumer.reclaim_stale(timezone.now() - STALE_AFTER)
        if reclaimed:
            self.stdout.write(f"consume: put back {reclaimed} stale claim(s)")
        try:
            report = consumer.run_once(batch=batch)
        except db.Error:
            log.exception("consume: the database went away, ending the process")
            raise
        self.stdout.write(report.line())
