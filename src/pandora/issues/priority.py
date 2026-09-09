from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

HIGH = "high"
MEDIUM = "medium"
LOW = "low"

ORDER = {LOW: 0, MEDIUM: 1, HIGH: 2}

_BY_LEVEL = {
    "fatal": HIGH,
    "error": MEDIUM,
    "warning": LOW,
    "info": LOW,
    "debug": LOW,
}


@dataclass(frozen=True)
class Inputs:
    """Everything the derivation is allowed to look at.

    Keeping it a value rather than an Issue is what lets the rule be tested
    without a database and reused by the sweep, the ingest path and the admin.
    """

    level: str
    open_episode_count: int = 0
    user_count: int = 0
    escalating: bool = False


def derive(inputs: Inputs) -> str:
    """Rank an issue the way a person reading the stream would.

    Level decides the floor. Three things raise it: an alert that is still
    firing, a fault that has reached more people than the threshold, and an
    issue that escalated out of being ignored. Nothing lowers it — a person
    who disagrees sets the priority by hand, and that choice is kept.
    """
    base = _BY_LEVEL.get(inputs.level, LOW)
    if inputs.escalating:
        return HIGH
    if inputs.open_episode_count > 0:
        return _raise(base)
    if _crowded(inputs.user_count):
        return _raise(base)
    return base


def _raise(current: str) -> str:
    if current == LOW:
        return MEDIUM
    return HIGH


def _crowded(user_count: int) -> bool:
    threshold = settings.PANDORA_PRIORITY_USER_THRESHOLD
    if threshold <= 0:
        return False
    return user_count >= threshold


def at_least(value: str) -> list[str]:
    wanted = ORDER.get(value)
    if wanted is None:
        return []
    return [name for name, rank in ORDER.items() if rank >= wanted]
