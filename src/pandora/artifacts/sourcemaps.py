from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
VLQ_SHIFT = 5
VLQ_BASE = 1 << VLQ_SHIFT
VLQ_MASK = VLQ_BASE - 1
VLQ_CONTINUATION = VLQ_BASE
INDEX = {char: position for position, char in enumerate(BASE64)}


@dataclass(frozen=True)
class Position:
    source: str
    line: int
    column: int
    name: str = ""
    context: list[str] | None = None


class SourceMapError(ValueError):
    pass


@dataclass
class SourceMap:
    """A parsed `.map`, ready to answer where a minified frame came from.

    Decoded once and cached, because a map is hundreds of kilobytes and a stack
    trace asks it the same question a dozen times.
    """

    sources: list[str]
    names: list[str]
    contents: list[str | None]
    mappings: dict[int, list[tuple[int, int, int, int, int]]]

    def lookup(self, line: int, column: int) -> Position | None:
        """The original position for a 1-based line and a 0-based column."""
        segments = self.mappings.get(line - 1)
        if not segments:
            return None
        found = None
        for segment in segments:
            if segment[0] > column:
                break
            found = segment
        if found is None:
            found = segments[0]
        _generated, source_index, source_line, source_column, name_index = found
        if source_index >= len(self.sources):
            return None
        return Position(
            source=self.sources[source_index],
            line=source_line + 1,
            column=source_column,
            name=self._name(name_index),
            context=self._context(source_index, source_line),
        )

    def _name(self, index: int) -> str:
        if index < 0 or index >= len(self.names):
            return ""
        return self.names[index]

    def _context(self, source_index: int, line: int) -> list[str] | None:
        body = self.contents[source_index]
        if body is None:
            return None
        return body.splitlines()


def parse(raw: str | bytes) -> SourceMap:
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise SourceMapError(f"source map is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise SourceMapError("source map is not a JSON object")
    if "mappings" not in document:
        raise SourceMapError("source map carries no mappings")

    sources = [str(name) for name in document.get("sources") or []]
    root = str(document.get("sourceRoot") or "")
    if root:
        sources = [f"{root.rstrip('/')}/{name.lstrip('/')}" for name in sources]
    contents_raw = document.get("sourcesContent") or []
    contents: list[str | None] = []
    for index in range(len(sources)):
        if index < len(contents_raw) and isinstance(contents_raw[index], str):
            contents.append(contents_raw[index])
        else:
            contents.append(None)
    return SourceMap(
        sources=sources,
        names=[str(name) for name in document.get("names") or []],
        contents=contents,
        mappings=_decode(str(document["mappings"])),
    )


def debug_id_of(document: Any) -> str:
    if not isinstance(document, dict):
        return ""
    for key in ("debug_id", "debugId"):
        value = document.get(key)
        if value:
            return str(value)
    return ""


def _decode(mappings: str) -> dict[int, list[tuple[int, int, int, int, int]]]:
    decoded: dict[int, list[tuple[int, int, int, int, int]]] = {}
    source_index = 0
    source_line = 0
    source_column = 0
    name_index = 0
    for line_number, line in enumerate(mappings.split(";")):
        generated_column = 0
        segments = []
        for segment in line.split(","):
            if not segment:
                continue
            values = _vlq(segment)
            generated_column += values[0]
            if len(values) >= 4:
                source_index += values[1]
                source_line += values[2]
                source_column += values[3]
            if len(values) >= 5:
                name_index += values[4]
            segments.append(
                (
                    generated_column,
                    source_index,
                    source_line,
                    source_column,
                    name_index if len(values) >= 5 else -1,
                )
            )
        if segments:
            decoded[line_number] = segments
    return decoded


def _vlq(segment: str) -> list[int]:
    values = []
    shift = 0
    accumulator = 0
    for char in segment:
        digit = INDEX.get(char)
        if digit is None:
            raise SourceMapError(f"{char!r} is not a base64 VLQ digit")
        accumulator += (digit & VLQ_MASK) << shift
        if digit & VLQ_CONTINUATION:
            shift += VLQ_SHIFT
            continue
        negative = accumulator & 1
        value = accumulator >> 1
        if negative:
            value = -value
        values.append(value)
        accumulator = 0
        shift = 0
    return values
