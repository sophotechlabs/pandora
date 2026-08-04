from __future__ import annotations

from typing import Any

from django.http import HttpRequest


def dashboard_callback(
    request: HttpRequest,
    context: dict[str, Any],
) -> dict[str, Any]:
    return context
