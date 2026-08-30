from __future__ import annotations

import re
from dataclasses import dataclass, field

PYTHON_HEADER = re.compile(r"^Traceback \(most recent call last\):")
PYTHON_FRAME = re.compile(
    r'^\s*File "(?P<filename>[^"]+)", line (?P<lineno>\d+), in (?P<function>.+)$'
)
PYTHON_EXCEPTION = re.compile(
    r"^(?P<module>[\w.]+\.)?(?P<type>[A-Za-z_][\w]*(?:Error|Exception|Warning|Exit))"
    r"(?::\s*(?P<value>.*))?$"
)

JAVA_HEADER = re.compile(
    r"^(?:Exception in thread \"[^\"]+\"\s+)?"
    r"(?P<type>(?:[\w$]+\.)*[\w$]*(?:Exception|Error|Throwable))"
    r"(?::\s*(?P<value>.*))?$"
)
JAVA_FRAME = re.compile(r"^\s*at\s+(?P<location>[\w$./<>]+)\((?P<source>[^)]*)\)\s*$")

GO_HEADER = re.compile(r"^panic:\s*(?P<value>.*)$")
GO_FUNCTION = re.compile(r"^(?P<function>[\w./*()\[\]]+\(.*\))$")
GO_LOCATION = re.compile(r"^\s+(?P<filename>[^\s:]+):(?P<lineno>\d+)")

NODE_HEADER = re.compile(
    r"^(?P<type>[A-Za-z_][\w]*(?:Error|Exception))(?::\s*(?P<value>.*))?$"
)
NODE_FRAME = re.compile(
    r"^\s*at\s+(?:(?P<function>[^(]+?)\s+\()?(?P<filename>[^\s()]+?)"
    r":(?P<lineno>\d+):(?P<colno>\d+)\)?\s*$"
)

MAX_FRAMES = 100


@dataclass
class Parsed:
    kind: str = ""
    value: str = ""
    module: str = ""
    language: str = ""
    frames: list[dict] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.kind or self.frames)


def parse(text: str) -> Parsed:
    """Turn a stack trace a log line carried into the frames the UI already renders.

    Every cluster has services nobody will ever instrument — a third-party chart,
    an operator, someone's Go binary from 2021 — and those are the ones that page
    you. The transport is a POST; this parser set is the work.
    """
    if not text:
        return Parsed()
    lines = text.splitlines()
    for parser in (_python, _java, _go, _node):
        parsed = parser(lines)
        if parsed.found:
            return parsed
    return Parsed()


def _python(lines: list[str]) -> Parsed:
    if not any(PYTHON_HEADER.match(line) for line in lines):
        return Parsed()
    frames = []
    for index, line in enumerate(lines):
        match = PYTHON_FRAME.match(line)
        if match is None:
            continue
        frame = {
            "filename": match.group("filename"),
            "lineno": int(match.group("lineno")),
            "function": match.group("function").strip(),
        }
        context = _next_code(lines, index)
        if context:
            frame["context_line"] = context
        frames.append(frame)
    kind, value, module = _python_exception(lines)
    return Parsed(
        kind=kind,
        value=value,
        module=module,
        language="python",
        frames=frames[:MAX_FRAMES],
    )


def _python_exception(lines: list[str]) -> tuple[str, str, str]:
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("File ", "Traceback")):
            continue
        match = PYTHON_EXCEPTION.match(stripped)
        if match is None:
            continue
        module = (match.group("module") or "").rstrip(".")
        return match.group("type"), (match.group("value") or "").strip(), module
    return ("", "", "")


def _next_code(lines: list[str], index: int) -> str:
    following = index + 1
    if following >= len(lines):
        return ""
    candidate = lines[following]
    if PYTHON_FRAME.match(candidate):
        return ""
    return candidate.strip()


def _java(lines: list[str]) -> Parsed:
    frames = _matches(JAVA_FRAME, lines)
    if not frames:
        return Parsed()
    header = JAVA_HEADER.match(lines[0].strip())
    kind = ""
    value = ""
    module = ""
    if header is not None:
        full = header.group("type")
        module, _, kind = full.rpartition(".")
        value = (header.group("value") or "").strip()
    parsed = [_java_frame(match) for match in frames[:MAX_FRAMES]]
    return Parsed(kind=kind, value=value, module=module, language="java", frames=parsed)


def _java_frame(match: re.Match[str]) -> dict:
    location = match.group("location")
    source = match.group("source")
    frame: dict = {"function": location}
    package, _, _method = location.rpartition(".")
    if package:
        frame["module"] = package
    if ":" in source:
        filename, _, lineno = source.rpartition(":")
        frame["filename"] = filename
        if lineno.isdigit():
            frame["lineno"] = int(lineno)
    elif source:
        frame["filename"] = source
    return frame


def _go(lines: list[str]) -> Parsed:
    header = None
    start = 0
    for index, line in enumerate(lines):
        header = GO_HEADER.match(line)
        if header is not None:
            start = index
            break
    if header is None:
        return Parsed()
    frames: list[dict] = []
    index = start + 1
    while index < len(lines) and len(frames) < MAX_FRAMES:
        function = GO_FUNCTION.match(lines[index].strip())
        if function is None:
            index += 1
            continue
        frame: dict = {"function": function.group("function")}
        if index + 1 < len(lines):
            location = GO_LOCATION.match(lines[index + 1])
            if location is not None:
                frame["filename"] = location.group("filename")
                frame["lineno"] = int(location.group("lineno"))
                index += 1
        frames.append(frame)
        index += 1
    return Parsed(
        kind="panic",
        value=header.group("value").strip(),
        language="go",
        frames=frames,
    )


def _matches(pattern: re.Pattern[str], lines: list[str]) -> list[re.Match[str]]:
    found = (pattern.match(line) for line in lines)
    return [match for match in found if match is not None]


def _node(lines: list[str]) -> Parsed:
    frames = _matches(NODE_FRAME, lines)
    if not frames:
        return Parsed()
    header = NODE_HEADER.match(lines[0].strip())
    kind = ""
    value = ""
    if header is not None:
        kind = header.group("type")
        value = (header.group("value") or "").strip()
    parsed = []
    for match in frames[:MAX_FRAMES]:
        frame: dict = {
            "filename": match.group("filename"),
            "lineno": int(match.group("lineno")),
            "colno": int(match.group("colno")),
        }
        function = match.group("function")
        if function:
            frame["function"] = function.strip()
        parsed.append(frame)
    return Parsed(kind=kind, value=value, language="javascript", frames=parsed)
