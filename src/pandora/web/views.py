from __future__ import annotations

from http import HTTPStatus
from importlib import metadata

from django import db
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from pandora.core import database
from pandora.issues import reporting

DISTRIBUTION = "pandora"
UNKNOWN_VERSION = "unknown"


def version() -> str:
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return UNKNOWN_VERSION


def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "version": version()})


def ready(request: HttpRequest) -> JsonResponse:
    try:
        with db.connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except db.Error as error:
        return JsonResponse(
            {"status": "unavailable", "detail": str(error)},
            status=HTTPStatus.SERVICE_UNAVAILABLE,
        )
    database.refresh_size()
    reporting.refresh(timezone.now())
    return JsonResponse({"status": "ok", "version": version()})
