from __future__ import annotations

import re
from collections.abc import Iterable

from django.conf import settings

UUID = "<uuid>"
URL = "<url>"
EMAIL = "<email>"
IP = "<ip>"
DATE = "<date>"
HEX = "<hex>"
NUMBER = "<n>"

PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[a-z][a-z0-9+.\-]*://\S+", re.IGNORECASE), URL),
    (re.compile(r"[^\s:@]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), EMAIL),
    (
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            re.IGNORECASE,
        ),
        UUID,
    ),
    (
        re.compile(
            r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?)?",
        ),
        DATE,
    ),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), IP),
    (re.compile(r"\b(?:[0-9a-f]{1,4}:){3,}[0-9a-f]{0,4}\b", re.IGNORECASE), IP),
    (re.compile(r"\b[0-9a-f]{8,}\b", re.IGNORECASE), HEX),
    (re.compile(r"\b\d+\b"), NUMBER),
)


def enabled() -> bool:
    return bool(settings.PANDORA_GROUPING_NORMALISE)


def value(text: str) -> str:
    """Replace what moves between two occurrences of the same fault.

    A UUID, a request id, a pod suffix, a timestamp and a bare number are the
    parts of a fingerprint that mint one issue per occurrence. They stay on the
    event, where they are worth reading; they leave the key, where they are not.

    A number is replaced only when it is its own token: `v1` and `http2` are
    names, the 47 in "retry 47" is not, and the word boundary is the whole rule.
    """
    normalised = text
    for pattern, placeholder in PATTERNS:
        normalised = pattern.sub(placeholder, normalised)
    return normalised


def parts(values: Iterable[str]) -> list[str]:
    if not enabled():
        return list(values)
    return [value(text) for text in values]


def label(text: str) -> str:
    if not enabled():
        return text
    return value(text)
