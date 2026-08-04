from __future__ import annotations

import time
from typing import Any

import prometheus_client
from django.core.management.base import BaseCommand, CommandError

from pandora.am import client as am_client
from pandora.am import reconcile as reconcile_alerts


class Command(BaseCommand):
    help = "Correct open episodes against Alertmanager's live alert set"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--loop",
            type=int,
            default=0,
            metavar="SECONDS",
            help="keep running, one pass every SECONDS (default: one pass)",
        )
        parser.add_argument(
            "--project",
            default="",
            help="project slug, when several Alertmanager tokens exist",
        )
        parser.add_argument(
            "--environment",
            default="",
            help="token environment, when several Alertmanager tokens exist",
        )
        parser.add_argument(
            "--metrics-port",
            type=int,
            default=0,
            help="serve Prometheus metrics on this port (default: off)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        reconciler = self._reconciler(options)
        port = options["metrics_port"]
        if port > 0:
            prometheus_client.start_http_server(port)
            self.stdout.write(f"reconcile: metrics on :{port}")

        interval = options["loop"]
        try:
            self._run(reconciler, interval)
        except KeyboardInterrupt:
            self.stdout.write("reconcile: stopped")

    def _reconciler(self, options: dict[str, Any]) -> reconcile_alerts.Reconciler:
        try:
            scope = reconcile_alerts.resolve_scope(
                options["project"].strip(),
                options["environment"].strip(),
            )
        except reconcile_alerts.ScopeError as error:
            raise CommandError(str(error)) from error
        try:
            client = am_client.from_settings()
        except am_client.AlertmanagerError as error:
            raise CommandError(str(error)) from error
        return reconcile_alerts.Reconciler(scope, client)

    def _run(self, reconciler: reconcile_alerts.Reconciler, interval: int) -> None:
        while True:
            self._cycle(reconciler)
            if interval <= 0:
                return
            time.sleep(interval)

    def _cycle(self, reconciler: reconcile_alerts.Reconciler) -> None:
        report = reconciler.cycle()
        if report.error:
            self.stderr.write(
                f"reconcile: could not read alertmanager — {report.error}"
            )
            return
        self.stdout.write(
            f"reconcile: {report.alerts} alerts, "
            f"{report.open_episodes} open episodes, "
            f"{report.opened} opened, {report.closed} closed, "
            f"{report.missing} missing"
        )
