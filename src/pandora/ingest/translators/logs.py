from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pandora.ingest.translators import stacks
from pandora.issues.models import Level

MESSAGE_KEYS = ("message", "msg", "log", "event", "body", "short_message")
LEVEL_KEYS = ("level", "severity", "levelname", "log.level", "severity_text")
STACK_KEYS = (
    "stack",
    "stacktrace",
    "stack_trace",
    "exception",
    "error.stack",
    "traceback",
    "exc_info",
)
KIND_KEYS = ("error.kind", "exception_type", "error_type", "exc_type")
LOGGER_KEYS = ("logger", "logger_name", "log.logger", "source")
SERVICE_KEYS = ("service", "service.name", "app", "application", "container_name")
ENVIRONMENT_KEYS = ("environment", "env", "deployment.environment")
RELEASE_KEYS = ("release", "version", "service.version")
TIMESTAMP_KEYS = ("timestamp", "time", "@timestamp", "ts", "date")

LEVELS = {
    "trace": Level.DEBUG,
    "debug": Level.DEBUG,
    "info": Level.INFO,
    "information": Level.INFO,
    "notice": Level.INFO,
    "warn": Level.WARNING,
    "warning": Level.WARNING,
    "error": Level.ERROR,
    "err": Level.ERROR,
    "critical": Level.FATAL,
    "fatal": Level.FATAL,
    "alert": Level.FATAL,
    "emergency": Level.FATAL,
    "panic": Level.FATAL,
}
TAG_LIMIT = 24
VALUE_LIMIT = 200


class LogError(ValueError):
    pass


def parse_lines(body: bytes) -> list[dict[str, Any]]:
    """One JSON object per line, which is what every shipper already speaks.

    Vector, rsyslog, journald and a CloudWatch drain all produce it, so the door
    is a POST and a page of config rather than an agent to install.
    """
    rows = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError as error:
            raise LogError(f"line is not valid JSON: {error}") from error
        if not isinstance(parsed, dict):
            raise LogError("each line must be a JSON object")
        rows.append(parsed)
    return rows


def to_event(row: Mapping[str, Any]) -> dict[str, Any]:
    """Turn one log line into the Sentry event shape the translator already takes.

    Everything downstream — grouping, triage, tag stats, the whole UI — is then
    unchanged, which is the reason to reuse the shape rather than invent one.
    """
    message = _first(row, MESSAGE_KEYS) or ""
    level = _level(_first(row, LEVEL_KEYS))
    payload: dict[str, Any] = {
        "level": level,
        "platform": "other",
        "logentry": {"formatted": str(message)},
        "tags": _tags(row),
    }

    environment = _first(row, ENVIRONMENT_KEYS)
    if environment:
        payload["environment"] = str(environment)
    release = _first(row, RELEASE_KEYS)
    if release:
        payload["release"] = str(release)
    logger = _first(row, LOGGER_KEYS)
    if logger:
        payload["logger"] = str(logger)
    timestamp = _first(row, TIMESTAMP_KEYS)
    if timestamp:
        payload["timestamp"] = str(timestamp)

    trace = _stack_text(row)
    if trace:
        parsed = stacks.parse(trace)
        if parsed.found:
            payload["exception"] = {"values": [_exception(parsed, row, message)]}
    elif _first(row, KIND_KEYS):
        payload["exception"] = {
            "values": [{"type": str(_first(row, KIND_KEYS)), "value": str(message)}]
        }
    return payload


def _exception(parsed: Any, row: Mapping[str, Any], message: Any) -> dict[str, Any]:
    kind = parsed.kind or str(_first(row, KIND_KEYS) or "Error")
    value = parsed.value or str(message)
    exception: dict[str, Any] = {"type": kind, "value": value}
    if parsed.module:
        exception["module"] = parsed.module
    if parsed.frames:
        exception["stacktrace"] = {"frames": parsed.frames}
    return exception


def _stack_text(row: Mapping[str, Any]) -> str:
    for key in STACK_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, Mapping):
            nested = value.get("stacktrace") or value.get("stack")
            if isinstance(nested, str) and nested.strip():
                return nested
    return ""


def _level(value: Any) -> str:
    if value is None:
        return Level.ERROR
    text = str(value).strip().lower()
    return LEVELS.get(text, Level.ERROR)


def _tags(row: Mapping[str, Any]) -> dict[str, str]:
    tags = {}
    for key in (*SERVICE_KEYS, "host", "hostname", "pod", "namespace", "node"):
        value = row.get(key)
        if value is None:
            continue
        tags[key.replace(".", "_")] = str(value)[:VALUE_LIMIT]
    declared = row.get("tags")
    if isinstance(declared, Mapping):
        for key, value in declared.items():
            if len(tags) >= TAG_LIMIT:
                break
            tags[str(key)[:VALUE_LIMIT]] = str(value)[:VALUE_LIMIT]
    return tags


def _first(row: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


OTLP_SEVERITIES = {
    1: "trace",
    2: "trace",
    3: "trace",
    4: "trace",
    5: "debug",
    6: "debug",
    7: "debug",
    8: "debug",
    9: "info",
    10: "info",
    11: "info",
    12: "info",
    13: "warn",
    14: "warn",
    15: "warn",
    16: "warn",
    17: "error",
    18: "error",
    19: "error",
    20: "error",
    21: "fatal",
    22: "fatal",
    23: "fatal",
    24: "fatal",
}
EXCEPTION_TYPE = "exception.type"
EXCEPTION_MESSAGE = "exception.message"
EXCEPTION_STACK = "exception.stacktrace"


def from_otlp(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten an OTLP/JSON logs request into the rows the log door already takes.

    Relay takes OpenTelemetry at its own path and pandora carries the OTel SDK
    already. Logs with an exception attached become issues through the same
    translator; traces are spans, and spans are a trap.
    """
    rows = []
    for resource in _list(document.get("resourceLogs")):
        attributes = _attributes(_get(resource, "resource", "attributes"))
        for scope in _list(resource.get("scopeLogs")):
            logger = _get(scope, "scope", "name")
            for record in _list(scope.get("logRecords")):
                rows.append(_record(record, attributes, logger))
    return rows


def _record(
    record: Mapping[str, Any], resource: dict[str, str], logger: Any
) -> dict[str, Any]:
    attributes = _attributes(record.get("attributes"))
    row: dict[str, Any] = dict(resource)
    row.update(attributes)
    row["message"] = _value(record.get("body")) or ""
    row["level"] = _severity(record)
    if logger:
        row["logger"] = str(logger)
    stamp = record.get("timeUnixNano") or record.get("observedTimeUnixNano")
    if stamp:
        row["timestamp"] = _moment(stamp)
    if attributes.get(EXCEPTION_STACK):
        row["stack"] = attributes[EXCEPTION_STACK]
    if attributes.get(EXCEPTION_TYPE):
        row["error.kind"] = attributes[EXCEPTION_TYPE]
    if attributes.get(EXCEPTION_MESSAGE):
        row["message"] = attributes[EXCEPTION_MESSAGE]
    return row


def _severity(record: Mapping[str, Any]) -> str:
    text = record.get("severityText")
    if text:
        return str(text)
    number = record.get("severityNumber")
    if isinstance(number, int):
        return OTLP_SEVERITIES.get(number, "error")
    return "error"


def _attributes(raw: Any) -> dict[str, str]:
    found: dict[str, str] = {}
    for entry in _list(raw):
        key = entry.get("key")
        if not key:
            continue
        found[str(key)] = _value(entry.get("value")) or ""
    return found


def _value(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, Mapping):
        for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if key in raw:
                return str(raw[key])
        if "arrayValue" in raw:
            values = _get(raw, "arrayValue", "values") or []
            return ", ".join(_value(item) for item in values)
    return str(raw)


def _moment(raw: Any) -> str:
    try:
        nanos = int(raw)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(nanos / 1_000_000_000, tz=UTC).isoformat()


def _list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    return []


def _get(raw: Any, *keys: str) -> Any:
    current: Any = raw
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current
