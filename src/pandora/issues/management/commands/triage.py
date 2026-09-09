from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from pandora.issues import escalation


class Command(BaseCommand):
    help = "Escalate quiet issues that came back, resolve stale ones, rank the rest"

    def handle(self, *args: Any, **options: Any) -> None:
        report = escalation.run(timezone.now())
        for line in report.lines():
            self.stdout.write(f"triage: {line}")
