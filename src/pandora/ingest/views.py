from __future__ import annotations

import io
import json
import logging
import secrets
import zlib
from http import HTTPStatus
from typing import Any

import brotli
import zstandard as zstd
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from pandora.core.models import DsnKey, IngestToken, TokenScope, TokenSource
from pandora.ingest import json_payload, monitors, sizes
from pandora.ingest.gate import Verdict, get_gate
from pandora.ingest.models import ProcessedEvent, RawEnvelope
from pandora.ingest.queue import get_queue
from pandora.ingest.translators import envelope as envelope_translator
from pandora.ingest.translators import logs as log_translator
from pandora.issues.models import Issue, UserReport
from pandora.releases import sessions
from pandora.scrub import service as scrub

BEARER_PREFIX = "Bearer "
SENTRY_AUTH_HEADER = "X-Sentry-Auth"
SENTRY_AUTH_PREFIX = "sentry "
SENTRY_KEY_FIELD = "sentry_key"
GZIP_ENCODINGS = ("gzip", "x-gzip")
DEFLATE_ENCODING = "deflate"
BROTLI_ENCODING = "br"
ZSTD_ENCODING = "zstd"
DECODE_ERRORS = (OSError, EOFError, zlib.error, brotli.error, zstd.ZstdError)
AUTO_WBITS = 47
READ_SIZE = 64 * 1024

log = logging.getLogger(__name__)
SESSION_ITEMS = ("session", "sessions")
LOG_LINE_LIMIT = 500
REPORT_ITEMS = ("user_report", "feedback")


def _refused(verdict: Verdict) -> JsonResponse:
    response = JsonResponse({"detail": verdict.reason}, status=verdict.status)
    for name, value in verdict.headers().items():
        response[name] = value
    return response


@csrf_exempt
def am_webhook(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse(
            {"detail": "method not allowed"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    token = _token_for(request)
    if token is None:
        log.warning("alertmanager ingest rejected an unknown or missing token")
        return JsonResponse(
            {"detail": "unknown or missing ingest token"},
            status=HTTPStatus.UNAUTHORIZED,
        )

    verdict = get_gate().check(token.project_id, _content_length(request))
    if not verdict.allowed:
        return _refused(verdict)

    try:
        payload = json_payload.loads(request.body)
    except ValueError:
        return JsonResponse(
            {"detail": "body is not valid JSON"},
            status=HTTPStatus.BAD_REQUEST,
        )
    if not isinstance(payload, dict):
        return JsonResponse(
            {"detail": "body is not a JSON object"},
            status=HTTPStatus.BAD_REQUEST,
        )

    rule = scrub.dropped_by(payload, token.project)
    if rule is not None:
        scrub.record_drop(rule, TokenSource.AM)
        return JsonResponse({"dropped": rule.name}, status=HTTPStatus.OK)

    envelope = RawEnvelope.objects.create(
        project=token.project,
        source=TokenSource.AM,
        environment=token.environment,
        payload=payload,
    )
    get_queue().publish(envelope.pk)
    return JsonResponse({"id": envelope.pk}, status=HTTPStatus.OK)


@csrf_exempt
def envelope(request: HttpRequest, project_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse(
            {"detail": "method not allowed"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    key = _dsn_key(request, project_id)
    if key is None:
        log.warning("envelope ingest rejected an unknown or missing DSN key")
        return JsonResponse(
            {"detail": "unknown or missing DSN key"},
            status=HTTPStatus.UNAUTHORIZED,
        )

    body, refusal = _read_body(request, key)
    if refusal is not None:
        return refusal

    try:
        parsed = envelope_translator.parse_envelope(body)
    except envelope_translator.EnvelopeError as error:
        log.warning(
            "envelope ingest refused %s bytes from project %s: %s",
            len(body),
            key.project_id,
            error,
        )
        return JsonResponse({"detail": str(error)}, status=HTTPStatus.BAD_REQUEST)

    events = envelope_translator.event_items(parsed)
    taken = len(events)
    for item in parsed.items:
        if item.type in REPORT_ITEMS:
            _accept_user_report(key, item)
            taken += 1
            continue
        if item.type in SESSION_ITEMS:
            if not sizes.fits(item.type, len(item.payload)):
                log.warning("envelope ingest dropped an oversized %s item", item.type)
                continue
            _accept_session(key, item)
            taken += 1
    dropped = len(parsed.items) - taken
    if dropped:
        log.info("envelope ingest acked and dropped %s unhandled items", dropped)

    for item in events:
        _accept_event(key, item, parsed.event_id)
    return JsonResponse({"id": parsed.event_id}, status=HTTPStatus.OK)


def _read_body(request: HttpRequest, key: DsnKey) -> tuple[bytes, JsonResponse | None]:
    """The gate, the size cap and the decoder, once for every door.

    A door that skips any of them is a hole, so there is one way through rather
    than one per endpoint.
    """
    verdict = get_gate().check(key.project_id, _content_length(request))
    if not verdict.allowed:
        return (b"", _refused(verdict))

    try:
        return (_decoded(request), None)
    except _TooLarge:
        return (
            b"",
            JsonResponse(
                {"detail": "oversized"},
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            ),
        )
    except DECODE_ERRORS:
        return (
            b"",
            JsonResponse(
                {"detail": "body is not decodable"},
                status=HTTPStatus.BAD_REQUEST,
            ),
        )


def _accept_session(key: DsnKey, item: envelope_translator.Item) -> None:
    try:
        payload = json_payload.loads(item.payload)
    except ValueError:
        log.warning("envelope ingest dropped a session item that is not valid JSON")
        return
    sessions.accept(key.project, payload, timezone.now())


@csrf_exempt
def store(request: HttpRequest, project_id: int) -> JsonResponse:
    """The older bare-JSON endpoint, still spoken by old SDKs and by curl.

    A strictly simpler parse than the envelope this already handles, and it
    removes a whole class of *why does my client not work*.
    """
    if request.method != "POST":
        return JsonResponse(
            {"detail": "method not allowed"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    key = _dsn_key(request, project_id)
    if key is None:
        return JsonResponse(
            {"detail": "unknown or missing DSN key"},
            status=HTTPStatus.UNAUTHORIZED,
        )

    body, refusal = _read_body(request, key)
    if refusal is not None:
        return refusal

    try:
        payload = json_payload.loads(body)
    except ValueError:
        return JsonResponse(
            {"detail": "body is not valid JSON"}, status=HTTPStatus.BAD_REQUEST
        )
    if not isinstance(payload, dict):
        return JsonResponse(
            {"detail": "body is not a JSON object"}, status=HTTPStatus.BAD_REQUEST
        )

    event_id = str(payload.get("event_id", "")) or secrets.token_hex(16)
    payload["event_id"] = event_id
    _store_event(key, payload)
    return JsonResponse({"id": event_id}, status=HTTPStatus.OK)


@csrf_exempt
def logs(request: HttpRequest, project_id: int) -> JsonResponse:
    """One NDJSON endpoint, fed by Vector, rsyslog, journald or a CloudWatch drain.

    Sentry only makes issues from its own SDK events. Every cluster has services
    nobody will ever instrument, and those are the ones that page you.
    """
    if request.method != "POST":
        return JsonResponse(
            {"detail": "method not allowed"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    key = _dsn_key(request, project_id)
    if key is None:
        return JsonResponse(
            {"detail": "unknown or missing DSN key"},
            status=HTTPStatus.UNAUTHORIZED,
        )

    body, refusal = _read_body(request, key)
    if refusal is not None:
        return refusal

    try:
        rows = log_translator.parse_lines(body)
    except log_translator.LogError as error:
        return JsonResponse({"detail": str(error)}, status=HTTPStatus.BAD_REQUEST)

    taken = _accept_rows(key, rows, TokenSource.LOG)
    return JsonResponse({"accepted": taken, "received": len(rows)})


@csrf_exempt
def otlp_logs(request: HttpRequest, project_id: int) -> JsonResponse:
    """`/v1/logs` only. Traces are spans, and spans are the trap."""
    if request.method != "POST":
        return JsonResponse(
            {"detail": "method not allowed"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    key = _dsn_key(request, project_id)
    if key is None:
        return JsonResponse(
            {"detail": "unknown or missing DSN key"},
            status=HTTPStatus.UNAUTHORIZED,
        )

    body, refusal = _read_body(request, key)
    if refusal is not None:
        return refusal

    try:
        document = json_payload.loads(body)
    except ValueError:
        return JsonResponse(
            {"detail": "body is not valid JSON"}, status=HTTPStatus.BAD_REQUEST
        )
    if not isinstance(document, dict):
        return JsonResponse(
            {"detail": "body is not a JSON object"}, status=HTTPStatus.BAD_REQUEST
        )

    rows = log_translator.from_otlp(document)
    taken = _accept_rows(key, rows, TokenSource.OTLP)
    return JsonResponse({"accepted": taken, "received": len(rows)})


def _accept_rows(key: DsnKey, rows: list[dict], source: str) -> int:
    taken = 0
    for row in rows[:LOG_LINE_LIMIT]:
        payload = log_translator.to_event(row)
        payload["event_id"] = secrets.token_hex(16)
        if not sizes.fits(sizes.EVENT, len(json.dumps(payload))):
            log.warning("log ingest dropped a line over the per-item limit")
            continue
        _store_event(key, payload, source=source)
        taken += 1
    return taken


def _store_event(key: DsnKey, payload: dict, source: str = TokenSource.SDK) -> None:
    rule = scrub.dropped_by(payload, key.project)
    if rule is not None:
        scrub.record_drop(rule, source)
        return

    stored = RawEnvelope.objects.create(
        project=key.project,
        source=source,
        environment=str(payload.get("environment", "")),
        payload=payload,
    )
    get_queue().publish(stored.pk)


@csrf_exempt
def check_in(
    request: HttpRequest, project_id: int, slug: str, sentry_key: str
) -> JsonResponse:
    """The dedicated cron endpoint the protocol already defines.

    A monitor is upserted from the check-in itself, so a job that reports is a
    job that is watched — there is no configuration step and no per-monitor cost.
    """
    if request.method != "POST":
        return JsonResponse(
            {"detail": "method not allowed"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    key = _key_for(project_id, sentry_key)
    if key is None:
        return JsonResponse(
            {"detail": "unknown or missing DSN key"},
            status=HTTPStatus.UNAUTHORIZED,
        )

    body, refusal = _read_body(request, key)
    if refusal is not None:
        return refusal

    if not sizes.fits(sizes.CHECK_IN, len(body)):
        return JsonResponse(
            {"detail": "oversized"},
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )

    payload = {}
    if body:
        try:
            parsed = json_payload.loads(body)
        except ValueError:
            return JsonResponse(
                {"detail": "body is not valid JSON"}, status=HTTPStatus.BAD_REQUEST
            )
        if isinstance(parsed, dict):
            payload = parsed

    status = str(payload.get("status", monitors.OK))
    if status not in monitors.STATUSES:
        return JsonResponse(
            {"detail": f"{status!r} is not a check-in status"},
            status=HTTPStatus.BAD_REQUEST,
        )

    schedule = payload.get("monitor_config") or {}
    if not isinstance(schedule, dict):
        return JsonResponse(
            {"detail": "monitor_config must be a JSON object"},
            status=HTTPStatus.BAD_REQUEST,
        )
    try:
        monitor = monitors.check_in(
            key.project,
            slug,
            status,
            timezone.now(),
            environment=payload.get("environment", ""),
            interval_minutes=schedule.get("interval_minutes"),
            margin_minutes=schedule.get("checkin_margin"),
            max_runtime_minutes=schedule.get("max_runtime"),
        )
    except monitors.InvalidMonitor as error:
        return JsonResponse(
            {"detail": str(error)},
            status=HTTPStatus.BAD_REQUEST,
        )
    return JsonResponse({"id": monitor.slug, "status": monitor.status})


def _key_for(project_id: int, presented: str) -> DsnKey | None:
    return (
        DsnKey.objects.filter(project_id=project_id, public_key=presented, active=True)
        .select_related("project")
        .first()
    )


def _accept_user_report(key: DsnKey, item: envelope_translator.Item) -> None:
    try:
        payload = json_payload.loads(item.payload)
    except ValueError:
        log.warning("envelope ingest dropped a user report that is not valid JSON")
        return
    if not isinstance(payload, dict):
        return
    event_id = str(payload.get("event_id", "")).strip()
    if not event_id:
        return
    UserReport.objects.create(
        project=key.project,
        issue=_issue_for_event(key.project_id, event_id),
        event_id=event_id[:64],
        name=str(payload.get("name", ""))[:200],
        email=str(payload.get("email", ""))[:254],
        comments=str(payload.get("comments", ""))[:5000],
    )


def _issue_for_event(project_id: int, sentry_id: str) -> Issue | None:
    processed = (
        ProcessedEvent.objects.filter(project_id=project_id, event_id=sentry_id)
        .select_related("issue")
        .first()
    )
    if processed is None:
        return None
    return processed.issue


def _accept_event(key: DsnKey, item: envelope_translator.Item, fallback: str) -> None:
    if not sizes.fits(sizes.EVENT, len(item.payload)):
        log.warning("envelope ingest dropped an event item over the per-item limit")
        return
    try:
        payload = json_payload.loads(item.payload)
    except ValueError:
        log.warning("envelope ingest dropped an event item that is not valid JSON")
        return
    if not isinstance(payload, dict):
        log.warning("envelope ingest dropped an event item that is not a JSON object")
        return
    payload.setdefault("event_id", fallback)

    rule = scrub.dropped_by(payload, key.project)
    if rule is not None:
        scrub.record_drop(rule, TokenSource.SDK)
        return

    _store_event(key, payload)


def _dsn_key(request: HttpRequest, project_id: int) -> DsnKey | None:
    presented = _presented_key(request)
    if not presented:
        return None
    return (
        DsnKey.objects.filter(
            project_id=project_id,
            public_key=presented,
            active=True,
        )
        .select_related("project")
        .first()
    )


def _presented_key(request: HttpRequest) -> str:
    header = request.headers.get(SENTRY_AUTH_HEADER, "")
    if header.lower().startswith(SENTRY_AUTH_PREFIX):
        parsed = _auth_fields(header[len(SENTRY_AUTH_PREFIX) :])
        if parsed:
            return parsed
    return request.GET.get(SENTRY_KEY_FIELD, "").strip()


def _auth_fields(raw: str) -> str:
    for part in raw.split(","):
        name, separator, value = part.partition("=")
        if not separator:
            continue
        if name.strip().lower() == SENTRY_KEY_FIELD:
            return value.strip()
    return ""


class _TooLarge(Exception):
    pass


def _decoded(request: HttpRequest) -> bytes:
    encoding = request.headers.get("Content-Encoding", "").strip().lower()
    limit = sizes.limit(sizes.ENVELOPE)
    raw = _raw(request, limit)
    if encoding in GZIP_ENCODINGS or encoding == DEFLATE_ENCODING:
        return _inflate(raw, limit)
    if encoding == BROTLI_ENCODING:
        return _brotli(raw, limit)
    if encoding == ZSTD_ENCODING:
        return _zstd(raw, limit)
    return raw


def _raw(request: HttpRequest, limit: int) -> bytes:
    """The request body, including the one shape Django will not read.

    Django sizes its stream from `Content-Length` alone, so a chunked request
    reads as empty — and `@sentry/node` sends every envelope chunked. Draining
    the WSGI stream is the difference between accepting that SDK and refusing it
    with a 400 nobody can explain.
    """
    if request.headers.get("Transfer-Encoding", "").strip().lower() != "chunked":
        return request.body
    stream = getattr(request, "environ", {}).get("wsgi.input")
    if stream is None:
        return request.body
    return _drained(stream, limit)


def _drained(stream: Any, limit: int) -> bytes:
    pieces = []
    total = 0
    while total <= limit:
        piece = stream.read(min(READ_SIZE, limit + 1 - total))
        if not piece:
            break
        pieces.append(piece)
        total += len(piece)
    if total > limit:
        raise _TooLarge
    return b"".join(pieces)


def _brotli(raw: bytes, limit: int) -> bytes:
    machine = brotli.Decompressor()
    pieces = []
    total = 0
    piece = machine.process(raw, limit + 1)
    while True:
        pieces.append(piece)
        total += len(piece)
        if total > limit:
            raise _TooLarge
        if machine.is_finished():
            return b"".join(pieces)
        if machine.can_accept_more_data():
            raise brotli.error("incomplete brotli body")
        piece = machine.process(b"", limit + 1 - total)


def _zstd(raw: bytes, limit: int) -> bytes:
    machine = zstd.ZstdDecompressor()
    with machine.stream_reader(io.BytesIO(raw)) as reader:
        return _drained(reader, limit)


def _inflate(raw: bytes, limit: int) -> bytes:
    machine = zlib.decompressobj(AUTO_WBITS)
    body = machine.decompress(raw, limit + 1)
    if len(body) > limit:
        raise _TooLarge
    return body


def _token_for(request: HttpRequest) -> IngestToken | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith(BEARER_PREFIX):
        return None
    presented = header[len(BEARER_PREFIX) :].strip()
    if not presented:
        return None

    candidates = IngestToken.objects.filter(
        source=TokenSource.AM,
        scope=TokenScope.INGEST,
        active=True,
    ).select_related("project")
    for candidate in candidates:
        if secrets.compare_digest(candidate.token, presented):
            return candidate
    return None


def _content_length(request: HttpRequest) -> int:
    raw = request.headers.get("Content-Length", "")
    try:
        return int(raw)
    except ValueError:
        pass
    if request.headers.get("Transfer-Encoding", "").strip().lower() == "chunked":
        return 0
    return len(request.body)
