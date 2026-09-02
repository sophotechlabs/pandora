from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from pandora.releases import service


class Command(BaseCommand):
    help = "Mark deployments that did not finish inside their rollout window"

    def handle(self, *args: Any, **options: Any) -> None:
        timed_out = service.time_out(timezone.now())
        self.stdout.write(f"rollouts: {timed_out} timed out")
