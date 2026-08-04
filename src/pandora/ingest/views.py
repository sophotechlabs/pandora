from __future__ import annotations

from http import HTTPStatus

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def am_webhook(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {"detail": "alertmanager ingest is not implemented yet"},
        status=HTTPStatus.NOT_IMPLEMENTED,
    )


@csrf_exempt
def envelope(request: HttpRequest, project_id: int) -> JsonResponse:
    return JsonResponse(
        {"detail": "envelope ingest is not implemented yet"},
        status=HTTPStatus.NOT_IMPLEMENTED,
    )
