from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

VALUE_MAX = 400
BREADCRUMB_MAX = 100
CARD_ORDER = ("sdk",)
REQUEST_PARTS = ("headers", "cookies", "env")
SCALAR_KEYS = (
    ("release", "Release"),
    ("dist", "Distribution"),
    ("environment", "Environment"),
    ("server_name", "Server"),
    ("transaction", "Transaction"),
    ("platform", "Platform"),
    ("culprit", "Culprit"),
)


@dataclass(frozen=True)
class SourceLine:
    number: int | None
    text: str
    current: bool


@dataclass(frozen=True)
class FrameRow:
    location: str
    filename: str
    lineno: int | None
    in_app: bool
    package: str
    context: tuple[SourceLine, ...]
    variables: tuple[tuple[str, str], ...]
    expanded: bool


@dataclass(frozen=True)
class ExceptionBlock:
    kind: str
    value: str
    module: str
    mechanism: str
    handled: str
    frames: tuple[FrameRow, ...]
    frames_omitted: int
    caused_by: bool


@dataclass(frozen=True)
class BreadcrumbRow:
    stamp: str
    category: str
    level: str
    message: str
    data: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ContextCard:
    title: str
    pairs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class EventBody:
    exceptions: tuple[ExceptionBlock, ...]
    breadcrumbs: tuple[BreadcrumbRow, ...]
    cards: tuple[ContextCard, ...]


def build(payload: Any) -> EventBody | None:
    if not isinstance(payload, Mapping) or not payload:
        return None

    exceptions = _exceptions(payload)
    if not exceptions:
        exceptions = _threads(payload)
    body = EventBody(
        exceptions=exceptions,
        breadcrumbs=_breadcrumbs(payload),
        cards=_cards(payload),
    )
    if not body.exceptions and not body.breadcrumbs and not body.cards:
        return None
    return body


def _entries(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    raw = payload.get(key)
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, Mapping)]


def _exceptions(payload: Mapping[str, Any]) -> tuple[ExceptionBlock, ...]:
    entries = _entries(payload, "exceptions")
    blocks = []
    for position, entry in enumerate(reversed(entries)):
        blocks.append(_exception(entry, caused_by=position > 0))
    return tuple(blocks)


def _exception(entry: Mapping[str, Any], *, caused_by: bool) -> ExceptionBlock:
    mechanism = entry.get("mechanism")
    kind = ""
    handled = ""
    if isinstance(mechanism, Mapping):
        kind = str(mechanism.get("type", ""))
        if mechanism.get("handled") is True:
            handled = "handled"
        if mechanism.get("handled") is False:
            handled = "unhandled"
    return ExceptionBlock(
        kind=str(entry.get("type", "")),
        value=str(entry.get("value", "")),
        module=str(entry.get("module", "")),
        mechanism=kind,
        handled=handled,
        frames=_frames(entry),
        frames_omitted=int(entry.get("frames_omitted", 0) or 0),
        caused_by=caused_by,
    )


def _threads(payload: Mapping[str, Any]) -> tuple[ExceptionBlock, ...]:
    for entry in _entries(payload, "threads"):
        if not entry.get("frames"):
            continue
        if entry.get("crashed") is False and entry.get("current") is not True:
            continue
        return (
            ExceptionBlock(
                kind=_thread_title(entry),
                value="",
                module="",
                mechanism="",
                handled="",
                frames=_frames(entry),
                frames_omitted=int(entry.get("frames_omitted", 0) or 0),
                caused_by=False,
            ),
        )
    return ()


def _thread_title(entry: Mapping[str, Any]) -> str:
    name = str(entry.get("name", "")).strip()
    if name:
        return f"Thread {name}"
    identifier = str(entry.get("id", "")).strip()
    if identifier:
        return f"Thread {identifier}"
    return "Thread"


def _frames(entry: Mapping[str, Any]) -> tuple[FrameRow, ...]:
    raw = entry.get("frames")
    if not isinstance(raw, list):
        return ()
    usable = [frame for frame in reversed(raw) if isinstance(frame, Mapping)]
    expanded = _expanded_index(usable)
    return tuple(
        _frame(frame, expanded=index == expanded) for index, frame in enumerate(usable)
    )


def _expanded_index(frames: Sequence[Mapping[str, Any]]) -> int:
    for index, frame in enumerate(frames):
        if frame.get("in_app") is True:
            return index
    if frames:
        return 0
    return -1


def _frame(raw: Mapping[str, Any], *, expanded: bool) -> FrameRow:
    lineno = raw.get("lineno")
    if not isinstance(lineno, int):
        lineno = None
    return FrameRow(
        location=_location(raw),
        filename=str(raw.get("filename") or raw.get("abs_path") or ""),
        lineno=lineno,
        in_app=raw.get("in_app") is True,
        package=str(raw.get("package", "")),
        context=_context(raw, lineno),
        variables=_pairs(raw.get("vars")),
        expanded=expanded,
    )


def _location(raw: Mapping[str, Any]) -> str:
    module = str(raw.get("module", "")).strip()
    function = str(raw.get("function") or raw.get("raw_function") or "").strip()
    if module and function:
        return f"{module} in {function}"
    if function:
        return function
    if module:
        return module
    return str(raw.get("filename") or raw.get("abs_path") or "")


def _context(raw: Mapping[str, Any], lineno: int | None) -> tuple[SourceLine, ...]:
    before = _text_lines(raw.get("pre_context"))
    after = _text_lines(raw.get("post_context"))
    current = raw.get("context_line")
    if not before and not after and not isinstance(current, str):
        return ()

    lines = []
    start = None
    if lineno is not None:
        start = lineno - len(before)
    for offset, text in enumerate(before):
        lines.append(
            SourceLine(number=_number(start, offset), text=text, current=False)
        )
    if isinstance(current, str):
        lines.append(SourceLine(number=lineno, text=current, current=True))
    for offset, text in enumerate(after):
        lines.append(
            SourceLine(number=_number(lineno, offset + 1), text=text, current=False)
        )
    return tuple(lines)


def _number(start: int | None, offset: int) -> int | None:
    if start is None:
        return None
    return start + offset


def _text_lines(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(line) for line in raw]


def _breadcrumbs(payload: Mapping[str, Any]) -> tuple[BreadcrumbRow, ...]:
    entries = _entries(payload, "breadcrumbs")[-BREADCRUMB_MAX:]
    return tuple(_breadcrumb(entry) for entry in reversed(entries))


def _breadcrumb(entry: Mapping[str, Any]) -> BreadcrumbRow:
    return BreadcrumbRow(
        stamp=_stamp(entry.get("timestamp")),
        category=str(entry.get("category") or entry.get("type") or ""),
        level=str(entry.get("level", "")),
        message=str(entry.get("message", "")),
        data=_pairs(entry.get("data")),
    )


def _stamp(raw: Any) -> str:
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        moment = datetime.fromtimestamp(float(raw), tz=UTC)
        return moment.strftime("%H:%M:%S")
    text = str(raw or "")
    if "T" in text:
        return text.split("T", 1)[1][:8]
    return text


def _cards(payload: Mapping[str, Any]) -> tuple[ContextCard, ...]:
    cards = []
    scalars = _scalars(payload)
    if scalars:
        cards.append(ContextCard(title="Event", pairs=scalars))
    user = _pairs(payload.get("user"))
    if user:
        cards.append(ContextCard(title="User", pairs=user))
    cards.extend(_request_cards(payload.get("request")))
    for key in CARD_ORDER:
        pairs = _pairs(payload.get(key))
        if pairs:
            cards.append(ContextCard(title=key.title(), pairs=pairs))
    contexts = payload.get("contexts")
    if isinstance(contexts, Mapping):
        for name, value in contexts.items():
            pairs = _pairs(value)
            if pairs:
                cards.append(ContextCard(title=str(name).title(), pairs=pairs))
    extra = _pairs(payload.get("extra"))
    if extra:
        cards.append(ContextCard(title="Extra", pairs=extra))
    return tuple(cards)


def _request_cards(raw: Any) -> list[ContextCard]:
    if not isinstance(raw, Mapping):
        return []
    scalars = {key: value for key, value in raw.items() if key not in REQUEST_PARTS}
    cards = []
    pairs = _pairs(scalars)
    if pairs:
        cards.append(ContextCard(title="Request", pairs=pairs))
    for key in REQUEST_PARTS:
        part = _pairs(raw.get(key))
        if part:
            cards.append(ContextCard(title=key.title(), pairs=part))
    return cards


def _scalars(payload: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    pairs = []
    for key, label in SCALAR_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            pairs.append((label, value))
    return tuple(pairs)


def _pairs(raw: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, Mapping):
        return ()
    pairs = []
    for key, value in raw.items():
        rendered = _render(value)
        if rendered:
            pairs.append((str(key), rendered))
    return tuple(pairs)


def _render(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:VALUE_MAX]
    if isinstance(value, bool | int | float):
        return str(value)
    return json.dumps(value, default=str, sort_keys=True)[:VALUE_MAX]
