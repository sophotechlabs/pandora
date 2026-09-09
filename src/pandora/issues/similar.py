from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pandora.issues.models import Issue

TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
CANDIDATE_LIMIT = 500
RESULT_LIMIT = 5
MIN_SCORE = 0.3
STOP_WORDS = frozenset(
    {
        "in",
        "at",
        "on",
        "the",
        "for",
        "and",
        "with",
        "from",
        "error",
        "exception",
        "failed",
        "failure",
        "none",
        "null",
        "self",
        "py",
        "js",
        "go",
    }
)


@dataclass(frozen=True)
class Match:
    issue: Issue
    score: float

    @property
    def percent(self) -> int:
        return round(self.score * 100)


def tokens(issue: Issue) -> frozenset[str]:
    """The words that decide whether two issues are the same kind of fault.

    Read from what grouping already computed rather than from the raw event,
    so a merge or a regroup moves the similarity with it and nothing has to be
    recomputed at write time.
    """
    parts: list[str] = [str(part) for part in (issue.fingerprint or [])]
    parts.append(issue.title)
    parts.append(issue.culprit)
    for key, value in (issue.grouping_labels or {}).items():
        parts.append(f"{key}:{value}")
    found = {
        word.lower()
        for part in parts
        for word in TOKEN.findall(part)
        if word.lower() not in STOP_WORDS
    }
    return frozenset(found)


def score(left: Iterable[str], right: Iterable[str]) -> float:
    """How much two token sets overlap, as a fraction of everything they name."""
    first = frozenset(left)
    second = frozenset(right)
    if not first or not second:
        return 0.0
    shared = len(first & second)
    if not shared:
        return 0.0
    return shared / len(first | second)


def candidates(issue: Issue, limit: int = CANDIDATE_LIMIT) -> Sequence[Issue]:
    return list(
        Issue.objects.filter(project_id=issue.project_id)
        .exclude(pk=issue.pk)
        .order_by("-last_seen", "-pk")[:limit]
    )


def similar(
    issue: Issue,
    limit: int = RESULT_LIMIT,
    minimum: float = MIN_SCORE,
) -> list[Match]:
    """Issues in the same project whose signature looks like this one's.

    No model and no training corpus: the comparison is set overlap over the
    grouping signature, computed at read time on a bounded candidate list. It
    answers *did we already decide about something like this* — which is the
    question the tab exists for.
    """
    mine = tokens(issue)
    if not mine:
        return []
    found = []
    for candidate in candidates(issue):
        overlap = score(mine, tokens(candidate))
        if overlap < minimum:
            continue
        found.append(Match(issue=candidate, score=overlap))
    found.sort(key=lambda match: (-match.score, -match.issue.pk))
    return found[:limit]
