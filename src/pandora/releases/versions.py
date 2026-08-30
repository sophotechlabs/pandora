from __future__ import annotations

import re

SEMVER = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?"
    r"(?:-(?P<pre>[0-9A-Za-z.\-]+))?(?:\+(?P<build>[0-9A-Za-z.\-]+))?$"
)
CALVER = re.compile(r"^v?(?P<year>\d{4})\.(?P<month>\d{1,2})(?:\.(?P<day>\d{1,3}))?$")
PAD = 6
SORT_LENGTH = 64


def sort_key(version: str) -> str:
    """A string that orders releases the way a person would.

    Semver first, calendar versions second, and anything else — a git sha, a
    build id — last and alphabetical, so `dateCreated` remains the tie-break the
    caller applies. Stored as a column because *resolved in the next release*
    and Countly's reoccurred semantics both need releases to be comparable in
    the database, not in Python.
    """
    text = version.strip()
    calendar = CALVER.match(text)
    if calendar is not None:
        return _numeric(
            "1",
            calendar.group("year"),
            calendar.group("month"),
            calendar.group("day") or "0",
            "",
        )
    parsed = SEMVER.match(text)
    if parsed is not None:
        return _numeric(
            "1",
            parsed.group("major"),
            parsed.group("minor"),
            parsed.group("patch") or "0",
            parsed.group("pre") or "",
        )
    return f"0{text}"[:SORT_LENGTH]


def is_parsed(version: str) -> bool:
    text = version.strip()
    return CALVER.match(text) is not None or SEMVER.match(text) is not None


def _numeric(prefix: str, *parts: str) -> str:
    head = "".join(part.rjust(PAD, "0") for part in parts[:-1])
    pre = parts[-1]
    if pre:
        return f"{prefix}{head}0{pre}"[:SORT_LENGTH]
    return f"{prefix}{head}1"[:SORT_LENGTH]
