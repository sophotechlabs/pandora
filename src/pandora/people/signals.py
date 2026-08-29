from __future__ import annotations

from typing import Any

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.http import HttpRequest

from pandora.people import audit

PASSWORD = "password"
VIA = "pandora_login_via"


def _via(request: HttpRequest | None) -> str:
    return getattr(request, VIA, PASSWORD)


@receiver(user_logged_in)
def on_logged_in(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    audit.record(user.get_username(), audit.SIGN_IN, "", {"via": _via(request)})


@receiver(user_logged_out)
def on_logged_out(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    if user is None:
        return
    audit.record(user.get_username(), audit.SIGN_OUT, "", {"via": _via(request)})
