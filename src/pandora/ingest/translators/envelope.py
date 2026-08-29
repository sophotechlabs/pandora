from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ulid import ULID

from pandora.core.models import Project
from pandora.events import payload as payload_interfaces
from pandora.issues import grouping, lifecycle
from pandora.issues.models import Level
from pandora.scrub import service as scrub

EVENT_ITEM = "event"
DEFAULT_LEVEL = Level.ERROR
SENTRY_LEVELS = {
    "fatal": Level.FATAL,
    "critical": Level.FATAL,
    "error": Level.ERROR,
    "warning": Level.WARNING,
    "warn": Level.WARNING,
    "info": Level.INFO,
    "log": Level.INFO,
    "debug": Level.DEBUG,
}
DEFAULT_PLACEHOLDER = "{{ default }}"
UNKNOWN_TITLE = "Unknown event"
TITLE_MAX = 500
CULPRIT_MAX = 500
ENVIRONMENT_MAX = 100
ID_TIME_BYTES = 6
ID_RANDOM_BYTES = 10
TAG_VALUE_MAX = 200
NEWLINE = b"\n"


class EnvelopeError(ValueError):
    pass


@dataclass(frozen=True)
class Item:
    type: str
    headers: dict[str, Any]
    payload: bytes


@dataclass(frozen=True)
class Envelope:
    headers: dict[str, Any]
    items: list[Item] = field(default_factory=list)

    @property
    def event_id(self) -> str:
        return str(self.headers.get("event_id", ""))


def parse_envelope(body: bytes) -> Envelope:
    if not body.strip():
        raise EnvelopeError("envelope is empty")

    head, _, rest = body.partition(NEWLINE)
    headers = _json_object(head, "envelope header")
    items = []
    while rest.strip():
        item, rest = _next_item(rest)
        items.append(item)
    return Envelope(headers=headers, items=items)


def event_items(envelope: Envelope) -> list[Item]:
    return [item for item in envelope.items if item.type == EVENT_ITEM]


def event_id(project_id: int, sentry_event_id: str, timestamp: datetime) -> str:
    key = "|".join([str(project_id), "sdk", sentry_event_id])
    digest = hashlib.sha256(key.encode()).digest()
    millis = int(timestamp.timestamp() * 1000)
    millis = max(millis, 0)
    stamp = millis.to_bytes(ID_TIME_BYTES, "big")
    return str(ULID.from_bytes(stamp + digest[:ID_RANDOM_BYTES]))


def translate_event(
    payload: Any,
    project: Project,
    *,
    environment: str = "",
    received_at: datetime,
) -> lifecycle.Occurrence:
    if not isinstance(payload, Mapping):
        raise EnvelopeError("event item is not a JSON object")

    exception = _first_exception(payload)
    fingerprint = _fingerprint(payload, exception)
    title = _title(payload, exception)
    tags = _tags(payload)

    timestamp = _timestamp(payload.get("timestamp"))
    if timestamp is None:
        timestamp = received_at

    return lifecycle.Occurrence(
        fingerprint=fingerprint,
        fingerprint_hash=grouping.fingerprint_hash(fingerprint),
        grouping_labels={},
        am_fingerprint="",
        labels={},
        status=lifecycle.STATUS_FIRING,
        title=title[:TITLE_MAX],
        culprit=_culprit(exception)[:CULPRIT_MAX],
        level=_level(payload),
        message=scrub.scrub_message(_message(payload, exception, title)),
        starts_at=timestamp,
        ends_at=None,
        timestamp=received_at,
        tags=scrub.scrub_payload(tags, project),
        extra=scrub.scrub_payload(_extra(payload), project),
        payload=scrub.scrub_payload(payload_interfaces.normalize(payload), project),
        environment=_environment(payload, environment)[:ENVIRONMENT_MAX],
        source="sdk",
    )


def sentry_event_id(payload: Mapping[str, Any], fallback: str = "") -> str:
    raw = payload.get("event_id")
    if raw is None:
        return fallback
    value = str(raw).strip()
    if value:
        return value
    return fallback


def _next_item(rest: bytes) -> tuple[Item, bytes]:
    head, _, body = rest.partition(NEWLINE)
    headers = _json_object(head, "item header")

    length = headers.get("length")
    if isinstance(length, int) and length >= 0:
        payload = body[:length]
        if len(payload) < length:
            raise EnvelopeError("item payload is shorter than its declared length")
        remainder = body[length:]
        remainder = remainder.removeprefix(NEWLINE)
    else:
        payload, _, remainder = body.partition(NEWLINE)

    return Item(
        type=str(headers.get("type", "")),
        headers=headers,
        payload=payload,
    ), remainder


def _json_object(raw: bytes, what: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        raise EnvelopeError(f"{what} is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise EnvelopeError(f"{what} is not a JSON object")
    return parsed


def _first_exception(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    exception = payload.get("exception")
    values: Any = None
    if isinstance(exception, Mapping):
        values = exception.get("values")
    if isinstance(exception, list):
        values = exception
    if not isinstance(values, list):
        return None
    for value in reversed(values):
        if isinstance(value, Mapping):
            return value
    return None


def _fingerprint(
    payload: Mapping[str, Any], exception: Mapping[str, Any] | None
) -> list[str]:
    declared = payload.get("fingerprint")
    if not isinstance(declared, list):
        return _default_fingerprint(payload, exception)

    parts = []
    for entry in declared:
        text = str(entry)
        if text == DEFAULT_PLACEHOLDER:
            parts.extend(_default_fingerprint(payload, exception))
            continue
        parts.append(text)
    if not parts:
        return _default_fingerprint(payload, exception)
    return parts


def _default_fingerprint(
    payload: Mapping[str, Any], exception: Mapping[str, Any] | None
) -> list[str]:
    if exception is not None:
        kind = str(exception.get("type", "")).strip()
        module = str(exception.get("module", "")).strip()
        if kind:
            parts = (module, kind, *_frame_parts(exception))
            return [part for part in parts if part]
    template = _logentry_template(payload)
    if template:
        return [part for part in (_logger(payload), template) if part]
    message = str(payload.get("message", "")).strip()
    if message:
        return [message]
    return [UNKNOWN_TITLE]


def _frame_parts(exception: Mapping[str, Any]) -> tuple[str, ...]:
    frame = _top_frame(exception)
    if frame is None:
        return ()
    module = str(frame.get("module", "")).strip()
    function = str(frame.get("function", "")).strip()
    if module:
        return (module, function)
    if function:
        return (function,)
    return (str(frame.get("filename", "")).strip(),)


def _title(payload: Mapping[str, Any], exception: Mapping[str, Any] | None) -> str:
    if exception is not None:
        kind = str(exception.get("type", "")).strip()
        culprit = _culprit(exception)
        if kind and culprit:
            return f"{kind}: {culprit}"
        if kind:
            return kind
    template = _logentry_template(payload)
    if template:
        return template
    message = str(payload.get("message", "")).strip()
    if message:
        return message
    return UNKNOWN_TITLE


def _logger(payload: Mapping[str, Any]) -> str:
    return str(payload.get("logger", "")).strip()


def _logentry_template(payload: Mapping[str, Any]) -> str:
    logentry = payload.get("logentry")
    if not isinstance(logentry, Mapping):
        return ""
    message = str(logentry.get("message", "")).strip()
    if message:
        return message
    return str(logentry.get("formatted", "")).strip()


def _logentry(payload: Mapping[str, Any]) -> str:
    logentry = payload.get("logentry")
    if not isinstance(logentry, Mapping):
        return ""
    formatted = str(logentry.get("formatted", "")).strip()
    if formatted:
        return formatted
    return str(logentry.get("message", "")).strip()


def _culprit(exception: Mapping[str, Any] | None) -> str:
    frame = _top_frame(exception)
    if frame is None:
        return ""
    module = str(frame.get("module", "")).strip()
    function = str(frame.get("function", "")).strip()
    if module and function:
        return f"{module} in {function}"
    if function:
        return function
    filename = str(frame.get("filename", "")).strip()
    lineno = frame.get("lineno")
    if filename and isinstance(lineno, int):
        return f"{filename}:{lineno}"
    return filename


def _top_frame(exception: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if exception is None:
        return None
    stacktrace = exception.get("stacktrace")
    if not isinstance(stacktrace, Mapping):
        return None
    frames = stacktrace.get("frames")
    if not isinstance(frames, list):
        return None

    fallback = None
    for entry in reversed(frames):
        if not isinstance(entry, Mapping):
            continue
        if fallback is None:
            fallback = entry
        if entry.get("in_app") is True:
            return entry
    return fallback


def _level(payload: Mapping[str, Any]) -> str:
    level = str(payload.get("level", "")).strip().lower()
    return SENTRY_LEVELS.get(level, DEFAULT_LEVEL)


def _message(
    payload: Mapping[str, Any],
    exception: Mapping[str, Any] | None,
    title: str,
) -> str:
    raised = _exception_line(exception)
    if raised:
        return raised
    logentry = _logentry(payload)
    if logentry:
        return logentry
    message = str(payload.get("message", "")).strip()
    if message:
        return message
    return title


def _exception_line(exception: Mapping[str, Any] | None) -> str:
    if exception is None:
        return ""
    kind = str(exception.get("type", "")).strip()
    value = str(exception.get("value", "")).strip()
    if kind and value:
        return f"{kind}: {value}"
    return kind


def _environment(payload: Mapping[str, Any], fallback: str) -> str:
    environment = str(payload.get("environment", "")).strip()
    if environment:
        return environment
    return fallback


def _tags(payload: Mapping[str, Any]) -> dict[str, str]:
    raw = payload.get("tags")
    pairs: Sequence[tuple[Any, Any]] = ()
    if isinstance(raw, Mapping):
        pairs = list(raw.items())
    if isinstance(raw, list):
        pairs = [
            (entry[0], entry[1])
            for entry in raw
            if isinstance(entry, list) and len(entry) == 2
        ]

    tags = {}
    for key, value in pairs:
        tags[str(key)] = str(value)[:TAG_VALUE_MAX]

    for key in ("release", "server_name", "transaction"):
        value = str(payload.get(key, "")).strip()
        if value:
            tags.setdefault(key, value[:TAG_VALUE_MAX])
    return tags


def _extra(payload: Mapping[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    declared = payload.get("extra")
    if isinstance(declared, Mapping):
        extra["extra"] = {str(key): value for key, value in declared.items()}
    for key in ("platform", "sdk", "request", "contexts", "modules", "transaction"):
        value = payload.get(key)
        if value not in (None, "", {}, []):
            extra[key] = value
    return extra


def _timestamp(raw: Any) -> datetime | None:
    if isinstance(raw, int | float):
        return datetime.fromtimestamp(float(raw), tz=UTC)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
