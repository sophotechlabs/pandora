from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MAX_EXCEPTIONS = 25
MAX_THREADS = 25
MAX_FRAMES = 250
MAX_BREADCRUMBS = 100
MAX_CONTEXT_LINES = 20
MAX_VARS = 50
MAX_MODULES = 500
MAX_CONTEXTS = 50
MAX_KEYS = 100
MAX_ITEMS = 50
MAX_DEPTH = 5
MAX_STRING = 4096
MAX_LINE = 1024
MAX_NAME = 512

FRAME_TEXT = (
    "filename",
    "abs_path",
    "module",
    "function",
    "raw_function",
    "package",
    "platform",
    "instruction_addr",
    "addr_mode",
    "symbol",
    "symbol_addr",
    "image_addr",
)
FRAME_NUMBERS = ("lineno", "colno")
BREADCRUMB_TEXT = ("type", "category", "level", "event_id")
USER_TEXT = ("id", "username", "email", "ip_address", "name", "segment")
REQUEST_TEXT = ("url", "method", "query_string", "fragment", "protocol")
REQUEST_MAPPINGS = ("headers", "cookies", "env")
SDK_TEXT = ("name", "version")
TOP_TEXT = (
    "platform",
    "release",
    "dist",
    "server_name",
    "transaction",
    "environment",
    "culprit",
    "logger",
    "type",
)


def normalize(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}

    payload: dict[str, Any] = {}
    _put(payload, "exceptions", _exceptions(raw))
    _put(payload, "threads", _threads(raw))
    _put(payload, "breadcrumbs", _breadcrumbs(raw))
    _put(payload, "user", _user(raw))
    _put(payload, "request", _request(raw))
    _put(payload, "contexts", _contexts(raw))
    _put(payload, "sdk", _sdk(raw))
    _put(payload, "modules", _modules(raw))
    _put(payload, "logentry", _logentry(raw))
    _put(payload, "extra", _extra(raw))
    _put(payload, "debug_images", _debug_images(raw))
    for key in TOP_TEXT:
        _put(payload, key, _text(raw.get(key), MAX_NAME))
    return payload


def _put(target: dict[str, Any], key: str, value: Any) -> None:
    if value in (None, "", {}, [], ()):
        return
    target[key] = value


def _text(value: Any, limit: int = MAX_STRING) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        text = value
    else:
        text = str(value)
    return text.strip()[:limit]


def _line(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value[:MAX_LINE]


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    return None


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _values(raw: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    holder = raw.get(key)
    candidates: Any = None
    if isinstance(holder, Mapping):
        candidates = holder.get("values")
    if isinstance(holder, list):
        candidates = holder
    if not isinstance(candidates, list):
        return []
    return [entry for entry in candidates if isinstance(entry, Mapping)]


def _exceptions(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = _values(raw, "exception")[-MAX_EXCEPTIONS:]
    return [_exception(entry) for entry in entries]


def _exception(entry: Mapping[str, Any]) -> dict[str, Any]:
    built: dict[str, Any] = {}
    _put(built, "type", _text(entry.get("type"), MAX_NAME))
    _put(built, "value", _text(entry.get("value"), MAX_STRING))
    _put(built, "module", _text(entry.get("module"), MAX_NAME))
    _put(built, "thread_id", _text(entry.get("thread_id"), MAX_NAME))
    _put(built, "mechanism", _mechanism(entry.get("mechanism")))
    built.update(_stacktrace(entry))
    return built


def _mechanism(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    built: dict[str, Any] = {}
    _put(built, "type", _text(raw.get("type"), MAX_NAME))
    _put(built, "description", _text(raw.get("description"), MAX_STRING))
    _put(built, "help_link", _text(raw.get("help_link"), MAX_NAME))
    handled = _flag(raw.get("handled"))
    if handled is not None:
        built["handled"] = handled
    _put(built, "meta", _trim(raw.get("meta"), 1))
    return built


def _threads(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = _values(raw, "threads")[:MAX_THREADS]
    return [_thread(entry) for entry in entries]


def _thread(entry: Mapping[str, Any]) -> dict[str, Any]:
    built: dict[str, Any] = {}
    _put(built, "id", _text(entry.get("id"), MAX_NAME))
    _put(built, "name", _text(entry.get("name"), MAX_NAME))
    for key in ("crashed", "current", "main"):
        flag = _flag(entry.get(key))
        if flag is not None:
            built[key] = flag
    built.update(_stacktrace(entry))
    return built


def _stacktrace(entry: Mapping[str, Any]) -> dict[str, Any]:
    stacktrace = entry.get("stacktrace")
    if not isinstance(stacktrace, Mapping):
        return {}
    raw_frames = stacktrace.get("frames")
    if not isinstance(raw_frames, list):
        return {}

    usable = [frame for frame in raw_frames if isinstance(frame, Mapping)]
    omitted = max(0, len(usable) - MAX_FRAMES)
    kept = usable[-MAX_FRAMES:]
    built: dict[str, Any] = {"frames": [_frame(frame) for frame in kept]}
    if omitted:
        built["frames_omitted"] = omitted
    return built


def _frame(raw: Mapping[str, Any]) -> dict[str, Any]:
    built: dict[str, Any] = {}
    for key in FRAME_TEXT:
        _put(built, key, _text(raw.get(key), MAX_NAME))
    for key in FRAME_NUMBERS:
        number = _number(raw.get(key))
        if number is not None:
            built[key] = number
    in_app = _flag(raw.get("in_app"))
    if in_app is not None:
        built["in_app"] = in_app
    _put(built, "context_line", _line(raw.get("context_line")))
    _put(built, "pre_context", _lines(raw.get("pre_context")))
    _put(built, "post_context", _lines(raw.get("post_context")))
    _put(built, "vars", _vars(raw.get("vars")))
    return built


def _lines(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [_line(line) for line in raw[:MAX_CONTEXT_LINES]]


def _vars(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    trimmed: dict[str, Any] = {}
    for key, value in list(raw.items())[:MAX_VARS]:
        trimmed[_text(key, MAX_NAME)] = _trim(value, 1)
    return trimmed


def _breadcrumbs(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = _values(raw, "breadcrumbs")[-MAX_BREADCRUMBS:]
    return [_breadcrumb(entry) for entry in entries]


def _breadcrumb(entry: Mapping[str, Any]) -> dict[str, Any]:
    built: dict[str, Any] = {}
    for key in BREADCRUMB_TEXT:
        _put(built, key, _text(entry.get(key), MAX_NAME))
    _put(built, "message", _text(entry.get("message"), MAX_STRING))
    timestamp = entry.get("timestamp")
    number = _number(timestamp)
    if number is not None:
        built["timestamp"] = number
    else:
        _put(built, "timestamp", _text(timestamp, MAX_NAME))
    _put(built, "data", _trim(entry.get("data"), 1))
    return built


def _user(raw: Mapping[str, Any]) -> dict[str, Any]:
    user = raw.get("user")
    if not isinstance(user, Mapping):
        return {}
    built: dict[str, Any] = {}
    for key in USER_TEXT:
        _put(built, key, _text(user.get(key), MAX_NAME))
    _put(built, "geo", _trim(user.get("geo"), 1))
    _put(built, "data", _trim(user.get("data"), 1))
    return built


def _request(raw: Mapping[str, Any]) -> dict[str, Any]:
    request = raw.get("request")
    if not isinstance(request, Mapping):
        return {}
    built: dict[str, Any] = {}
    for key in REQUEST_TEXT:
        _put(built, key, _text(request.get(key), MAX_STRING))
    for key in REQUEST_MAPPINGS:
        _put(built, key, _pairs(request.get(key)))
    _put(built, "data", _trim(request.get("data"), 1))
    return built


def _pairs(raw: Any) -> dict[str, str]:
    items: Sequence[tuple[Any, Any]] = ()
    if isinstance(raw, Mapping):
        items = list(raw.items())
    if isinstance(raw, list):
        items = [
            (entry[0], entry[1])
            for entry in raw
            if isinstance(entry, list) and len(entry) == 2
        ]
    built: dict[str, str] = {}
    for key, value in items[:MAX_KEYS]:
        built[_text(key, MAX_NAME)] = _text(value, MAX_LINE)
    return built


def _contexts(raw: Mapping[str, Any]) -> dict[str, Any]:
    contexts = raw.get("contexts")
    if not isinstance(contexts, Mapping):
        return {}
    built: dict[str, Any] = {}
    for name, value in list(contexts.items())[:MAX_CONTEXTS]:
        if not isinstance(value, Mapping):
            continue
        trimmed = _trim(value, 1)
        if trimmed:
            built[_text(name, MAX_NAME)] = trimmed
    return built


def _sdk(raw: Mapping[str, Any]) -> dict[str, Any]:
    sdk = raw.get("sdk")
    if not isinstance(sdk, Mapping):
        return {}
    built: dict[str, Any] = {}
    for key in SDK_TEXT:
        _put(built, key, _text(sdk.get(key), MAX_NAME))
    _put(built, "integrations", _names(sdk.get("integrations")))
    return built


def _names(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    kept = [_text(entry, MAX_NAME) for entry in raw[:MAX_ITEMS]]
    return [entry for entry in kept if entry]


def _modules(raw: Mapping[str, Any]) -> dict[str, str]:
    modules = raw.get("modules")
    if not isinstance(modules, Mapping):
        return {}
    built: dict[str, str] = {}
    for name, version in list(modules.items())[:MAX_MODULES]:
        built[_text(name, MAX_NAME)] = _text(version, MAX_NAME)
    return built


def _logentry(raw: Mapping[str, Any]) -> dict[str, Any]:
    logentry = raw.get("logentry")
    if not isinstance(logentry, Mapping):
        return {}
    built: dict[str, Any] = {}
    _put(built, "message", _text(logentry.get("message"), MAX_STRING))
    _put(built, "formatted", _text(logentry.get("formatted"), MAX_STRING))
    params = logentry.get("params")
    if isinstance(params, list | Mapping):
        _put(built, "params", _trim(params, 1))
    return built


def _extra(raw: Mapping[str, Any]) -> dict[str, Any]:
    extra = raw.get("extra")
    if not isinstance(extra, Mapping):
        return {}
    return _trim(extra, 1)


def _debug_images(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    debug_meta = raw.get("debug_meta")
    if not isinstance(debug_meta, Mapping):
        return []
    images = debug_meta.get("images")
    if not isinstance(images, list):
        return []
    built = []
    for image in images[:MAX_ITEMS]:
        if not isinstance(image, Mapping):
            continue
        trimmed = _trim(image, 1)
        if trimmed:
            built.append(trimmed)
    return built


def _trim(value: Any, depth: int) -> Any:
    if isinstance(value, Mapping):
        if depth > MAX_DEPTH:
            return _text(value, MAX_LINE)
        return {
            _text(key, MAX_NAME): _trim(item, depth + 1)
            for key, item in list(value.items())[:MAX_KEYS]
        }
    if isinstance(value, list | tuple):
        if depth > MAX_DEPTH:
            return _text(value, MAX_LINE)
        return [_trim(item, depth + 1) for item in list(value)[:MAX_ITEMS]]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return _text(value, MAX_STRING)
