from __future__ import annotations

from collections.abc import Mapping
from typing import Any

VALUE_MAX = 200
USER_FIELDS = ("id", "username", "email", "ip_address")
USER_LABELS = {"ip_address": "ip"}
CONTEXT_TAGS = (
    ("browser", "browser", "name", "version"),
    ("os", "os", "name", "version"),
    ("runtime", "runtime", "name", "version"),
    ("device", "device", "model", ""),
)
HANDLED_VALUES = {True: "yes", False: "no"}


def derive(
    normalised: Mapping[str, Any],
    exception: Mapping[str, Any] | None,
) -> dict[str, str]:
    """The tags Sentry's own server writes for a caller that declared none.

    Every one of them is already inside the payload; a tag is what makes it
    filterable, countable and visible in the breakdown without a reader opening
    an event.
    """
    tags: dict[str, str] = {}
    _put(tags, "logger", _text(normalised.get("logger")))
    _put(tags, "user", _user(normalised.get("user")))
    _put(tags, "url", _url(normalised.get("request")))
    _put(tags, "method", _method(normalised.get("request")))
    _put(tags, "trace", _trace(normalised.get("contexts")))
    tags.update(_contexts(normalised.get("contexts")))
    tags.update(_mechanism(exception))
    return tags


def _put(tags: dict[str, str], key: str, value: str) -> None:
    if not value:
        return
    tags[key] = value[:VALUE_MAX]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if not isinstance(value, str | int | float):
        return ""
    return str(value).strip()


def _user(user: Any) -> str:
    if not isinstance(user, Mapping):
        return ""
    for field in USER_FIELDS:
        value = _text(user.get(field))
        if value:
            label = USER_LABELS.get(field, field)
            return f"{label}:{value}"
    return ""


def _url(request: Any) -> str:
    if not isinstance(request, Mapping):
        return ""
    return _text(request.get("url"))


def _method(request: Any) -> str:
    if not isinstance(request, Mapping):
        return ""
    return _text(request.get("method")).upper()


def _trace(contexts: Any) -> str:
    trace = _context(contexts, "trace")
    if trace is None:
        return ""
    return _text(trace.get("trace_id"))


def _contexts(contexts: Any) -> dict[str, str]:
    tags: dict[str, str] = {}
    for tag, name, primary, secondary in CONTEXT_TAGS:
        context = _context(contexts, name)
        if context is None:
            continue
        head = _text(context.get(primary))
        if not head:
            continue
        _put(tags, f"{tag}.name", head)
        tail = ""
        if secondary:
            tail = _text(context.get(secondary))
        if tail:
            _put(tags, tag, f"{head} {tail}")
            continue
        _put(tags, tag, head)
    return tags


def _context(contexts: Any, name: str) -> Mapping[str, Any] | None:
    if not isinstance(contexts, Mapping):
        return None
    context = contexts.get(name)
    if not isinstance(context, Mapping):
        return None
    return context


def _mechanism(exception: Any) -> dict[str, str]:
    if not isinstance(exception, Mapping):
        return {}
    mechanism = exception.get("mechanism")
    if not isinstance(mechanism, Mapping):
        return {}
    tags: dict[str, str] = {}
    _put(tags, "mechanism", _text(mechanism.get("type")))
    handled = mechanism.get("handled")
    if isinstance(handled, bool):
        _put(tags, "handled", HANDLED_VALUES[handled])
    return tags
