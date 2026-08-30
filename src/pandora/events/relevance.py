from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

from pandora.issues.models import Issue

DAY = timedelta(days=1)
MIN_KEEP = 1


@dataclass(frozen=True)
class Verdict:
    issue_id: int
    keep: int
    seen: int
    age_days: float

    @property
    def dropping(self) -> int:
        return max(0, self.seen - self.keep)


def enabled() -> bool:
    return bool(settings.PANDORA_RETENTION_BY_RELEVANCE)


def keep_for(seen: int, age_days: float) -> int:
    """How many copies of one issue are worth keeping at this age.

    A fixed day count throws away the one occurrence of the rare bug at the same
    rate as the thousandth copy of the noisy one. Bugsink's model is better for
    a single box: score by age *and* by how many similar events already exist,
    so the rare one survives and the flood is thinned.
    """
    budget = settings.PANDORA_RELEVANCE_BUDGET
    halvings = age_days / max(settings.PANDORA_RELEVANCE_HALF_LIFE_DAYS, 1)
    allowance = budget / (2**halvings)
    return max(MIN_KEEP, min(seen, round(allowance)))


def verdicts(now: datetime) -> list[Verdict]:
    found = []
    for issue in Issue.objects.all().only("pk", "event_count", "last_seen"):
        age = (now - issue.last_seen).total_seconds() / DAY.total_seconds()
        found.append(
            Verdict(
                issue_id=issue.pk,
                keep=keep_for(issue.event_count, age),
                seen=issue.event_count,
                age_days=age,
            )
        )
    return found
