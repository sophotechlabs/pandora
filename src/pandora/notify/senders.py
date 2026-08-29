from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

import requests
from django.conf import settings
from django.core.mail import send_mail

from pandora.notify.models import Delivery, Destination, DestinationKind

TIMEOUT = 10
SIGNATURE_HEADER = "X-Pandora-Signature"
EVENT_HEADER = "X-Pandora-Event"


class SendError(RuntimeError):
    pass


def send(destination: Destination, deliveries: list[Delivery]) -> None:
    handler = HANDLERS.get(destination.kind)
    if handler is None:
        raise SendError(f"{destination.kind} is not a destination kind")
    handler(destination, deliveries)


def _body(deliveries: list[Delivery]) -> dict[str, Any]:
    if len(deliveries) == 1:
        return dict(deliveries[0].payload)
    return {
        "event": "issue.digest",
        "count": len(deliveries),
        "deliveries": [delivery.payload for delivery in deliveries],
    }


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(destination: Destination, body: dict[str, Any], event: str) -> None:
    raw = json.dumps(body, sort_keys=True).encode()
    headers = {"Content-Type": "application/json", EVENT_HEADER: event}
    if destination.secret:
        headers[SIGNATURE_HEADER] = sign(destination.secret, raw)
    try:
        response = requests.post(
            destination.target.strip(), data=raw, headers=headers, timeout=TIMEOUT
        )
    except requests.RequestException as error:
        raise SendError(str(error)) from error
    if response.status_code >= 400:
        raise SendError(f"{response.status_code} from {destination.name}")


def send_webhook(destination: Destination, deliveries: list[Delivery]) -> None:
    body = _body(deliveries)
    _post(destination, body, str(body.get("event", "")))


def _lines(deliveries: list[Delivery]) -> list[str]:
    lines = []
    for delivery in deliveries:
        issue = delivery.payload.get("issue", {})
        lines.append(
            f"[{delivery.event}] {issue.get('title', '')} "
            f"({issue.get('project', '')}/{issue.get('environment', '')}) "
            f"{issue.get('url', '')}"
        )
    return lines


def send_chat(destination: Destination, deliveries: list[Delivery]) -> None:
    text = "\n".join(_lines(deliveries))
    field = "content" if destination.kind == DestinationKind.DISCORD else "text"
    _post(destination, {field: text}, "chat")


def send_email(destination: Destination, deliveries: list[Delivery]) -> None:
    recipients = [
        part.strip() for part in destination.target.split(",") if part.strip()
    ]
    if not recipients:
        raise SendError(f"{destination.name} has no recipients")
    lines = _lines(deliveries)
    subject = lines[0][:120]
    if len(deliveries) > 1:
        subject = f"Pandora: {len(deliveries)} issues need attention"
    sent = send_mail(
        subject=subject,
        message="\n".join(lines),
        from_email=settings.PANDORA_EMAIL_FROM or None,
        recipient_list=recipients,
        fail_silently=False,
    )
    if not sent:
        raise SendError(f"no mail accepted for {destination.name}")


HANDLERS: dict[str, Callable[[Destination, list[Delivery]], None]] = {
    DestinationKind.WEBHOOK: send_webhook,
    DestinationKind.EMAIL: send_email,
    DestinationKind.SLACK: send_chat,
    DestinationKind.DISCORD: send_chat,
    DestinationKind.TEAMS: send_chat,
}
