from __future__ import annotations

import re
from typing import Any

from pandora.issues import conditions

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.*]+)\s*\}\}")
DEFAULT = "default"


def render(text: str, document: Any) -> str:
    """Interpolate `{{ path.into.payload }}` from the occurrence being grouped.

    A path that reaches nothing renders as nothing, so a template can name a tag
    every event does not carry without producing the string `None`.
    """

    def substitute(match: re.Match[str]) -> str:
        path = match.group(1)
        found = conditions.resolve(path, document)
        if not found:
            return ""
        return _text(found[0])

    return PLACEHOLDER.sub(substitute, text)


def is_default(text: str) -> bool:
    match = PLACEHOLDER.fullmatch(text.strip())
    if match is None:
        return False
    return match.group(1) == DEFAULT


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)
