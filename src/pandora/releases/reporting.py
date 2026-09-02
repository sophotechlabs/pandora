from __future__ import annotations

from datetime import datetime, timedelta

from django.db.models import Count, Q
from prometheus_client import Gauge

from pandora.releases.models import Deploy, DeployState

WINDOW = timedelta(days=30)

DEPLOYS_PER_DAY = Gauge(
    "pandora_deploys_per_day",
    "Successful deployments per day over the reporting window",
    ["project", "environment"],
    multiprocess_mode="livemostrecent",
)
SUCCESSFUL_DEPLOYS = Gauge(
    "pandora_successful_deploys",
    "Successful deployments inside the reporting window",
    ["project", "environment"],
    multiprocess_mode="livemostrecent",
)


def counts(now: datetime) -> dict[tuple[str, str], int]:
    since = now - WINDOW
    rows = (
        Deploy.objects.values("release__project__slug", "environment")
        .annotate(
            succeeded=Count(
                "pk",
                filter=Q(
                    state=DeployState.SUCCEEDED,
                    started_at__gte=since,
                    started_at__lte=now,
                ),
            )
        )
        .order_by()
    )
    return {
        (row["release__project__slug"], row["environment"]): row["succeeded"]
        for row in rows
    }


def refresh(now: datetime) -> dict[tuple[str, str], float]:
    frequencies = {}
    for labels, total in counts(now).items():
        project, environment = labels
        frequency = total / WINDOW.days
        frequencies[labels] = frequency
        DEPLOYS_PER_DAY.labels(project=project, environment=environment).set(frequency)
        SUCCESSFUL_DEPLOYS.labels(project=project, environment=environment).set(total)
    return frequencies
