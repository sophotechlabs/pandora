from __future__ import annotations

import json
import logging
import secrets
import zlib
from http import HTTPStatus

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from pandora.core.models import DsnKey, IngestToken, TokenScope, TokenSource
from pandora.ingest.gate import Verdict, get_gate
from pandora.ingest.models import RawEnvelope
from pandora.ingest.queue import get_queue
from pandora.ingest.translators import envelope as envelope_translator
from pandora.scrub import service as scrub

BEARER_PREFIX = "Bearer "
SENTRY_AUTH_HEADER = "X-Sentry-Auth"
SENTRY_AUTH_PREFIX = "sentry "
SENTRY_KEY_FIELD = "sentry_key"
GZIP_ENCODINGS = ("gzip", "x-gzip")
DEFLATE_ENCODING = "deflate"
DECODE_ERRORS = (OSError, EOFError, zlib.error)
AUTO_WBITS = 47

log = logging.getLogger(__name__)


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

    verdict = get_gate().check(token, _content_length(request))
    if not verdict.allowed:
        return JsonResponse({"detail": verdict.reason}, status=verdict.status)

    try:
        payload = json.loads(request.body)
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

    verdict = _size_verdict(_content_length(request))
    if not verdict.allowed:
        return JsonResponse({"detail": verdict.reason}, status=verdict.status)

    try:
        body = _decoded(request)
    except _TooLarge:
        return JsonResponse(
            {"detail": "oversized"},
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )
    except DECODE_ERRORS:
        return JsonResponse(
            {"detail": "body is not decodable"},
            status=HTTPStatus.BAD_REQUEST,
        )

    try:
        parsed = envelope_translator.parse_envelope(body)
    except envelope_translator.EnvelopeError as error:
        return JsonResponse({"detail": str(error)}, status=HTTPStatus.BAD_REQUEST)

    events = envelope_translator.event_items(parsed)
    dropped = len(parsed.items) - len(events)
    if dropped:
        log.info("envelope ingest acked and dropped %s non-event items", dropped)

    for item in events:
        _accept_event(key, item, parsed.event_id)
    return JsonResponse({"id": parsed.event_id}, status=HTTPStatus.OK)


def _accept_event(key: DsnKey, item: envelope_translator.Item, fallback: str) -> None:
    try:
        payload = json.loads(item.payload)
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

    stored = RawEnvelope.objects.create(
        project=key.project,
        source=TokenSource.SDK,
        environment=str(payload.get("environment", "")),
        payload=payload,
    )
    get_queue().publish(stored.pk)


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


def _size_verdict(content_length: int) -> Verdict:
    if content_length > settings.PANDORA_INGEST_MAX_BYTES:
        return Verdict(
            allowed=False,
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            reason="oversized",
        )
    return Verdict(allowed=True)


def _decoded(request: HttpRequest) -> bytes:
    encoding = request.headers.get("Content-Encoding", "").strip().lower()
    limit = settings.PANDORA_INGEST_MAX_BYTES
    if encoding in GZIP_ENCODINGS:
        return _inflate(request.body, limit)
    if encoding == DEFLATE_ENCODING:
        return _inflate(request.body, limit)
    return request.body


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
        return len(request.body)
