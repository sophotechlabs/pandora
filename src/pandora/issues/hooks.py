from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string

log = logging.getLogger(__name__)


def resolve(setting: str) -> list[Callable[..., None]]:
    raw = getattr(settings, setting, "") or ""
    found = []
    for path in [part.strip() for part in raw.split(",") if part.strip()]:
        try:
            found.append(import_string(path))
        except ImportError:
            log.exception("hook %r cannot be imported and was skipped", path)
    return found


def fire(setting: str, *args: Any, **kwargs: Any) -> None:
    for hook in resolve(setting):
        try:
            hook(*args, **kwargs)
        except Exception:
            log.exception("hook %r failed and was skipped", hook)
