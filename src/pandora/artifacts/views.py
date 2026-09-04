from __future__ import annotations

import json
import logging
from http import HTTPStatus

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from pandora.artifacts import service
from pandora.core.models import IngestToken, TokenScope

BEARER_PREFIX = "Bearer "
log = logging.getLogger(__name__)


@csrf_exempt
def chunk_upload(request: HttpRequest, organization: str = "") -> JsonResponse:
    """What `sentry-cli` negotiates with, and then uploads to.

    A GET advertises what this server accepts; a POST takes chunks, each part
    named by the checksum of what it holds. Implementing the contract verbatim
    is the same play as speaking the envelope protocol: the tooling is already
    in everyone's CI, so there is no upload tool to write or maintain.
    """
    token = _token(request)
    if token is None:
        return JsonResponse({"detail": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)

    if request.method == "GET":
        return JsonResponse(service.chunk_options())
    if request.method != "POST":
        return JsonResponse(
            {"detail": "method not allowed"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    uploads = []
    for name in sorted(request.FILES):
        uploads.extend(request.FILES.getlist(name))

    if not uploads:
        return JsonResponse(
            {"detail": "no chunk was uploaded"}, status=HTTPStatus.BAD_REQUEST
        )
    if len(uploads) > service.MAX_CHUNKS_PER_REQUEST:
        return JsonResponse(
            {"detail": "too many chunks"},
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )
    total_size = 0
    for uploaded in uploads:
        if uploaded.size is None:
            return JsonResponse(
                {"detail": "chunk size is unknown"},
                status=HTTPStatus.BAD_REQUEST,
            )
        total_size += uploaded.size
    if total_size > service.MAX_REQUEST_SIZE:
        return JsonResponse(
            {"detail": "upload is too large"},
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )

    now = timezone.now()
    taken = []
    for uploaded in uploads:
        body = uploaded.read(service.MAX_REQUEST_SIZE + 1)
        try:
            taken.append(service.store_chunk(token.project, body, now))
        except service.ChunkTooLarge:
            return JsonResponse(
                {"detail": "oversized chunk"},
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
    return JsonResponse({"chunks": taken})


@csrf_exempt
def assemble(request: HttpRequest, organization: str = "") -> JsonResponse:
    """Join what was uploaded and answer with the state the protocol defines.

    `not_found` with a list is the useful answer: it tells the client exactly
    which chunks to send rather than failing an upload it could have finished.
    """
    token = _token(request)
    if token is None:
        return JsonResponse({"detail": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
    if request.method != "POST":
        return JsonResponse(
            {"detail": "method not allowed"},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    try:
        document = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse(
            {"detail": "body is not valid JSON"}, status=HTTPStatus.BAD_REQUEST
        )
    if not isinstance(document, dict):
        return JsonResponse(
            {"detail": "body is not a JSON object"}, status=HTTPStatus.BAD_REQUEST
        )

    checksum = document.get("checksum")
    chunks = document.get("chunks")
    if not isinstance(checksum, str) or not checksum.strip():
        return JsonResponse(
            {"detail": "checksum and chunks are both required"},
            status=HTTPStatus.BAD_REQUEST,
        )
    if not isinstance(chunks, list) or not chunks:
        return JsonResponse(
            {"detail": "checksum and chunks are both required"},
            status=HTTPStatus.BAD_REQUEST,
        )
    if len(chunks) > service.MAX_CHUNKS_PER_REQUEST:
        return JsonResponse(
            {"detail": "too many chunks"},
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )
    if not all(isinstance(row, str) and row.strip() for row in chunks):
        return JsonResponse(
            {"detail": "chunks must be strings"},
            status=HTTPStatus.BAD_REQUEST,
        )

    state, missing, detail = service.assemble(
        token.project, checksum.strip(), [row.strip() for row in chunks], timezone.now()
    )
    if state == service.STATE_ERROR:
        log.warning("artifact assemble refused: %s", detail)
    return JsonResponse(
        {"state": state, "missingChunks": missing, "detail": detail},
        status=HTTPStatus.OK,
    )


def _token(request: HttpRequest) -> IngestToken | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith(BEARER_PREFIX):
        return None
    presented = header[len(BEARER_PREFIX) :].strip()
    if not presented:
        return None
    return (
        IngestToken.objects.filter(
            token=presented,
            active=True,
            scope_grants__scope=TokenScope.ARTIFACTS,
        )
        .select_related("project")
        .first()
    )
