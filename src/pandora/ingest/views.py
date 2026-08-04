from __future__ import annotations

import json
import logging
import secrets
from http import HTTPStatus

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from pandora.core.models import IngestToken, TokenScope, TokenSource
from pandora.ingest.gate import get_gate
from pandora.ingest.models import RawEnvelope
from pandora.ingest.queue import get_queue

BEARER_PREFIX = "Bearer "

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
    return JsonResponse(
        {"detail": "envelope ingest is not implemented yet"},
        status=HTTPStatus.NOT_IMPLEMENTED,
    )


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
