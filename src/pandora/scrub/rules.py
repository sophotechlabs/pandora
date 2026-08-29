from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[redacted]"

KEYWORDS = (
    "password",
    "secret",
    "passwd",
    "api_key",
    "apikey",
    "auth",
    "credentials",
    "mysql_pwd",
    "privatekey",
    "private_key",
    "token",
    "bearer",
    "session",
    "cookie",
    "csrftoken",
)

CARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
MAX_DEPTH = 8


def _is_secret_key(key: str, keywords: Sequence[str]) -> bool:
    lowered = key.lower()
    return any(word in lowered for word in keywords)


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def mask_card(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        digits = re.sub(r"[ -]", "", match.group(0))
        if not _luhn(digits):
            return match.group(0)
        return REDACTED

    return CARD.sub(replace, text)


def mask_ip(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        parts = match.group(0).split(".")
        if any(int(part) > 255 for part in parts):
            return match.group(0)
        return ".".join([*parts[:3], "0"])

    return IPV4.sub(replace, text)


def scrub_value(value: Any, *, anonymise_ip: bool) -> Any:
    if not isinstance(value, str):
        return value
    masked = mask_card(value)
    if anonymise_ip:
        masked = mask_ip(masked)
    return masked


def scrub(
    payload: Any,
    *,
    keywords: Sequence[str] = KEYWORDS,
    safe_keys: Sequence[str] = (),
    anonymise_ip: bool = True,
    depth: int = 0,
) -> Any:
    if depth > MAX_DEPTH:
        return payload
    if isinstance(payload, Mapping):
        return {
            key: _scrub_entry(
                str(key),
                item,
                keywords=keywords,
                safe_keys=safe_keys,
                anonymise_ip=anonymise_ip,
                depth=depth,
            )
            for key, item in payload.items()
        }
    if isinstance(payload, list):
        return [
            scrub(
                item,
                keywords=keywords,
                safe_keys=safe_keys,
                anonymise_ip=anonymise_ip,
                depth=depth + 1,
            )
            for item in payload
        ]
    return scrub_value(payload, anonymise_ip=anonymise_ip)


def _scrub_entry(
    key: str,
    value: Any,
    *,
    keywords: Sequence[str],
    safe_keys: Sequence[str],
    anonymise_ip: bool,
    depth: int,
) -> Any:
    if key.lower() in {safe.lower() for safe in safe_keys}:
        return value
    if _is_secret_key(key, keywords):
        return REDACTED
    return scrub(
        value,
        keywords=keywords,
        safe_keys=safe_keys,
        anonymise_ip=anonymise_ip,
        depth=depth + 1,
    )
