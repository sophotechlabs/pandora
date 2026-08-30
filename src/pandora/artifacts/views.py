from __future__ import annotations

import logging
from http import HTTPStatus

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from pandora.artifacts import service
from pandora.artifacts.sourcemaps import SourceMapError
from pandora.core.models import IngestToken, TokenScope

BEARER_PREFIX = "Bearer "
log = logging.getLogger(__name__)


@csrf_exempt
def chunk_upload(request: HttpRequest, organization: str = "") -> JsonResponse:
    """The one endpoint `sentry-cli` negotiates with and uploads to.

    A GET advertises what this server accepts; a POST takes the bundle.
    """
    if request.method == "GET":
        if _token(request) is None:
            return JsonResponse(
                {"detail": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED
            )
        return JsonResponse(service.chunk_options())
    return _upload(request)


def _upload(request: HttpRequest) -> JsonResponse:
    """Take an artifact bundle from unmodified upload tooling.

    Implementing the contract verbatim is the same play as speaking the envelope
    protocol: `sentry-cli` and the bundler plugins are MIT-licensed and already
    in everyone's CI, so there is no upload tool to write or maintain.
    """
    if request.method != "POST":
        return JsonResponse(
            {"detail": "method not allowed"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    token = _token(request)
    if token is None:
        return JsonResponse({"detail": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)

    payload = _payload(request)
    if not payload:
        return JsonResponse(
            {"detail": "no bundle was uploaded"}, status=HTTPStatus.BAD_REQUEST
        )
    if len(payload) > service.MAX_REQUEST_SIZE:
        return JsonResponse(
            {"detail": "oversized"},
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )

    try:
        stored = service.store_bundle(token.project, payload, timezone.now())
    except SourceMapError as error:
        return JsonResponse({"detail": str(error)}, status=HTTPStatus.BAD_REQUEST)

    if not stored:
        return JsonResponse(
            {"detail": "no file in the bundle carried a debug id"},
            status=HTTPStatus.BAD_REQUEST,
        )
    return JsonResponse(
        {
            "bundles": [
                {"debug_id": row.bundle.debug_id, "files": row.files} for row in stored
            ]
        }
    )


def _payload(request: HttpRequest) -> bytes:
    names = sorted(request.FILES)
    if not names:
        return request.body
    return request.FILES.getlist(names[0])[0].read()


def _token(request: HttpRequest) -> IngestToken | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith(BEARER_PREFIX):
        return None
    presented = header[len(BEARER_PREFIX) :].strip()
    if not presented:
        return None
    return (
        IngestToken.objects.filter(
            token=presented, active=True, scope=TokenScope.INGEST
        )
        .select_related("project")
        .first()
    )
