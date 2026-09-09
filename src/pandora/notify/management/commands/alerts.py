from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from pandora.notify import metrics


class Command(BaseCommand):
    help = "Evaluate the metric monitors and open or close their alerts"

    def handle(self, *args: Any, **options: Any) -> None:
        report = metrics.sweep(timezone.now())
        for line in report.lines():
            self.stdout.write(f"alerts: {line}")
