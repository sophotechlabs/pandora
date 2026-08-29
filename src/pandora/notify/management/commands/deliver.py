from __future__ import annotations

import logging
import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from prometheus_client import start_http_server

from pandora.notify import deliver

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send queued notifications, once or in a loop"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--loop",
            type=int,
            default=0,
            help="seconds between passes; omit for a single pass",
        )
        parser.add_argument("--limit", type=int, default=deliver.BATCH)
        parser.add_argument(
            "--metrics-port",
            type=int,
            default=0,
            help="serve prometheus metrics from this process",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["limit"] < 1:
            raise CommandError("--limit must be at least 1")
        if options["metrics_port"]:
            start_http_server(options["metrics_port"])

        interval = options["loop"]
        if not interval:
            self._pass(options["limit"])
            return
        while True:
            self._pass(options["limit"])
            time.sleep(interval)

    def _pass(self, limit: int) -> None:
        report = deliver.run_once(timezone.now(), limit)
        self.stdout.write(
            f"deliver: {report.sent} sent, {report.retried} retried, "
            f"{report.failed} failed"
        )
