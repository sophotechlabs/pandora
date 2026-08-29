from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest

from pandora.people import oidc

ENVIRONMENT_TONES = {
    "local": "info",
    "dev": "info",
    "development": "info",
    "staging": "warning",
    "stage": "warning",
    "prod": "danger",
    "production": "danger",
}


def chrome(request: HttpRequest) -> dict[str, Any]:
    environment = settings.PANDORA_ENV.strip()
    return {
        "pandora_env": environment.upper(),
        "pandora_env_tone": ENVIRONMENT_TONES.get(environment.lower(), "info"),
        "sso_enabled": oidc.enabled(),
    }
