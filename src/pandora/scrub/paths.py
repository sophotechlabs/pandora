from __future__ import annotations

import fnmatch
from collections.abc import Mapping, Sequence
from typing import Any

from pandora.scrub.rules import REDACTED, mask_card, mask_ip

WILDCARD = "*"
DEEP = "**"


def split(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.split(".") if part)


def matches(parts: Sequence[str], trail: Sequence[str]) -> bool:
    if not parts:
        return not trail
    head, rest = parts[0], parts[1:]
    if head == DEEP:
        if matches(rest, trail):
            return True
        if not trail:
            return False
        return matches(parts, trail[1:])
    if not trail:
        return False
    if not fnmatch.fnmatchcase(trail[0], head):
        return False
    return matches(rest, trail[1:])


def apply(payload: Any, path: str, action: str) -> Any:
    return _walk(payload, split(path), (), action)


def _walk(node: Any, parts: Sequence[str], trail: tuple[str, ...], action: str) -> Any:
    if isinstance(node, Mapping):
        return {
            key: _apply_child(node[key], parts, (*trail, str(key)), action)
            for key in node
        }
    if isinstance(node, list):
        return [
            _apply_child(item, parts, (*trail, str(index)), action)
            for index, item in enumerate(node)
        ]
    return node


def _apply_child(
    value: Any, parts: Sequence[str], trail: tuple[str, ...], action: str
) -> Any:
    if matches(parts, trail):
        return _redact(value, action)
    return _walk(value, parts, trail, action)


def _redact(value: Any, action: str) -> Any:
    if action == "mask":
        if isinstance(value, str):
            return mask_ip(mask_card(value))
        return value
    return REDACTED
