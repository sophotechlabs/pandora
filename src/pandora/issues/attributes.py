from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Sum

from pandora.issues.models import TAG_OVERFLOW_VALUE, Issue, TagStat

MIN_SHARE = 0.25
MIN_LIFT = 2.0
MIN_COUNT = 3
LIMIT = 8


@dataclass(frozen=True)
class Attribute:
    key: str
    value: str
    share: float
    baseline: float
    lift: float
    sampled: bool

    @property
    def percent(self) -> int:
        return round(self.share * 100)

    @property
    def baseline_percent(self) -> int:
        return round(self.baseline * 100)


def distinguishing(issue: Issue) -> list[Attribute]:
    """Which tag values set this issue apart from the rest of the project.

    Sentry shows a tag distribution but never says which distribution is
    abnormal, which is the entire question. This is a share against the project
    baseline, computed at read time from the breakdown already on disk.
    """
    mine = _shares(TagStat.objects.filter(issue=issue))
    if not mine:
        return []
    theirs = _shares(
        TagStat.objects.filter(issue__project_id=issue.project_id).exclude(issue=issue)
    )
    found = []
    for (key, value), (share, count) in mine.items():
        if value == TAG_OVERFLOW_VALUE:
            continue
        if share < MIN_SHARE or count < MIN_COUNT:
            continue
        baseline = theirs.get((key, value), (0.0, 0))[0]
        lift = share / max(baseline, 1 / max(_total(theirs, key), 1))
        if baseline and lift < MIN_LIFT:
            continue
        found.append(
            Attribute(
                key=key,
                value=value,
                share=share,
                baseline=baseline,
                lift=lift,
                sampled=_sampled(issue, key),
            )
        )
    found.sort(key=lambda row: (-row.lift, -row.share, row.key))
    return found[:LIMIT]


def _shares(queryset: object) -> dict[tuple[str, str], tuple[float, int]]:
    rows = list(queryset.values("key", "value").annotate(total=Sum("count")))  # type: ignore[attr-defined]
    totals: dict[str, int] = {}
    for row in rows:
        totals[row["key"]] = totals.get(row["key"], 0) + row["total"]
    shares = {}
    for row in rows:
        whole = totals[row["key"]]
        if not whole:
            continue
        shares[(row["key"], row["value"])] = (row["total"] / whole, row["total"])
    return shares


def _total(shares: dict[tuple[str, str], tuple[float, int]], key: str) -> int:
    return sum(1 for (name, _) in shares if name == key)


def _sampled(issue: Issue, key: str) -> bool:
    return TagStat.objects.filter(
        issue=issue, key=key, value=TAG_OVERFLOW_VALUE
    ).exists()
