from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

LEVEL_VARIANTS = {
    "debug": "default",
    "info": "info",
    "warning": "warning",
    "error": "danger",
    "fatal": "danger",
}

TRIAGE_VARIANTS = {
    "new": "danger",
    "ack": "warning",
    "resolved": "success",
    "ignored": "default",
}

SOURCE_VARIANTS = {
    "firing": "danger",
    "resolved": "success",
}


@dataclass(frozen=True)
class Cell:
    text: str
    href: str | None = None
    variant: str | None = None
    external: bool = False


@dataclass(frozen=True)
class Column:
    label: str
    numeric: bool = False


@dataclass(frozen=True)
class Table:
    columns: tuple[Column, ...]
    rows: tuple[tuple[Cell, ...], ...]
    empty_message: str

    @property
    def column_count(self) -> int:
        return len(self.columns)


@dataclass(frozen=True)
class Kpi:
    label: str
    value: str | int
    hint: str


@dataclass(frozen=True)
class Bar:
    label: str
    count: int
    percent: int


def format_duration(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_stamp(stamp: datetime | None) -> str:
    if stamp is None:
        return "—"
    return timezone.localtime(stamp).strftime("%b %d, %H:%M")


def percent_of(count: int, total: int) -> int:
    if total <= 0:
        return 0
    return round(count * 100 / total)
