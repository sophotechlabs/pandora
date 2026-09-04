from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from typing import IO, Any, cast

from pandora.ingest import json_payload
from pandora.ingest.translators import envelope as translator

ATTACHMENT_ITEM = "attachment"
HEADER_LIMIT = 64 * 1024
HEADER_TOTAL_LIMIT = 1_000_000
ITEM_LIMIT = 1000
READ_SIZE = 64 * 1024


def _spooled_file() -> IO[bytes]:
    return tempfile.SpooledTemporaryFile(max_size=1024 * 1024)


class AttachmentTooLarge(translator.EnvelopeError):
    pass


@dataclass
class PendingAttachment:
    filename: str
    content_type: str
    attachment_type: str
    size: int
    sha256: str
    body: IO[bytes]

    def close(self) -> None:
        self.body.close()


@dataclass
class ParsedEnvelope:
    envelope: translator.Envelope
    attachments: list[PendingAttachment]
    size: int

    def close(self) -> None:
        for attachment in self.attachments:
            attachment.close()


def parse(
    stream: IO[bytes],
    attachment_limit: int,
    item_limit: int,
) -> ParsedEnvelope:
    stream.seek(0)
    header_bytes = 0
    size = 0
    line_limit = min(HEADER_LIMIT, item_limit)
    raw_header = cast(bytes, _line(stream, "envelope header", limit=line_limit))
    size += len(raw_header)
    header_bytes += len(raw_header)
    headers = _object(raw_header, "envelope header")
    items: list[translator.Item] = []
    attachments: list[PendingAttachment] = []
    attachment_bytes = 0
    try:
        while True:
            raw_item_header = _line(
                stream,
                "item header",
                allow_eof=True,
                limit=line_limit,
            )
            if raw_item_header is None:
                break
            item_header_size = len(raw_item_header)
            if item_header_size == 0:
                item_header_size = 1
            size += item_header_size
            header_bytes += item_header_size
            if header_bytes > HEADER_TOTAL_LIMIT:
                raise AttachmentTooLarge("envelope headers are too large")
            if not raw_item_header.strip():
                continue
            if len(items) + len(attachments) >= ITEM_LIMIT:
                raise AttachmentTooLarge("envelope has too many items")
            item_headers = _object(raw_item_header, "item header")
            item_type = str(item_headers.get("type", ""))
            length = _length(item_headers.get("length"))
            if item_type == ATTACHMENT_ITEM:
                if length is None:
                    raise translator.EnvelopeError("attachment length is required")
                attachment_bytes += length
                if attachment_bytes > attachment_limit:
                    raise AttachmentTooLarge("attachments are too large")
                attachment = _attachment(stream, item_headers, length)
                attachments.append(attachment)
                size += length
                _delimiter(stream)
                continue
            payload = _payload(stream, length, item_limit)
            size += len(payload)
            items.append(
                translator.Item(type=item_type, headers=item_headers, payload=payload)
            )
            if length is not None:
                _delimiter(stream)
    except Exception:
        for attachment in attachments:
            attachment.close()
        raise
    return ParsedEnvelope(
        envelope=translator.Envelope(headers=headers, items=items),
        attachments=attachments,
        size=size,
    )


def _line(
    stream: IO[bytes],
    what: str,
    allow_eof: bool = False,
    limit: int = HEADER_LIMIT,
) -> bytes | None:
    raw = stream.readline(limit + 1)
    if not raw and allow_eof:
        return None
    if not raw:
        raise translator.EnvelopeError("envelope is empty")
    if len(raw) > limit:
        raise AttachmentTooLarge(f"{what} is too large")
    return raw.removesuffix(b"\n")


def _object(raw: bytes, what: str) -> dict[str, Any]:
    try:
        parsed = json_payload.loads(raw)
    except ValueError as error:
        raise translator.EnvelopeError(f"{what} is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise translator.EnvelopeError(f"{what} is not a JSON object")
    return parsed


def _length(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise translator.EnvelopeError("item length is not a non-negative integer")
    if not isinstance(raw, int):
        raise translator.EnvelopeError("item length is not a non-negative integer")
    if raw < 0:
        raise translator.EnvelopeError("item length is not a non-negative integer")
    return raw


def _payload(stream: IO[bytes], length: int | None, limit: int) -> bytes:
    if length is not None:
        if length > limit:
            raise AttachmentTooLarge("item payload is too large")
        payload = stream.read(length)
        if len(payload) < length:
            raise translator.EnvelopeError(
                "item payload is shorter than its declared length"
            )
        return payload
    payload = stream.readline(limit + 1)
    if len(payload) > limit:
        raise AttachmentTooLarge("item payload is too large")
    return payload.removesuffix(b"\n")


def _attachment(
    stream: IO[bytes],
    headers: dict[str, Any],
    length: int,
) -> PendingAttachment:
    filename = headers.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        raise translator.EnvelopeError("attachment filename is required")
    filename = _metadata_text(filename, 255)
    if not filename:
        raise translator.EnvelopeError("attachment filename is required")
    body = _spooled_file()
    digest = hashlib.sha256()
    remaining = length
    while remaining:
        piece = stream.read(min(READ_SIZE, remaining))
        if not piece:
            body.close()
            raise translator.EnvelopeError(
                "item payload is shorter than its declared length"
            )
        body.write(piece)
        digest.update(piece)
        remaining -= len(piece)
    body.seek(0)
    return PendingAttachment(
        filename=filename,
        content_type=_metadata_text(headers.get("content_type", ""), 255),
        attachment_type=_metadata_text(headers.get("attachment_type", ""), 64),
        size=length,
        sha256=digest.hexdigest(),
        body=body,
    )


def _metadata_text(raw: Any, limit: int) -> str:
    value = str(raw).replace("\r", " ").replace("\n", " ").strip()
    return value[:limit]


def _delimiter(stream: IO[bytes]) -> None:
    delimiter = stream.read(1)
    if not delimiter:
        return
    if delimiter != b"\n":
        raise translator.EnvelopeError("item payload is not followed by a newline")
