from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pandora.core.models import Project, TokenSource
from pandora.ingest.models import RawEnvelope
from pandora.ingest.processor import process_envelope


@dataclass(frozen=True)
class DemoEvent:
    minutes_ago: int
    payload: dict[str, Any]


def _python_event(suffix: str, transaction_id: str) -> dict[str, Any]:
    return {
        "event_id": f"6f1c2d9e4b7a48c19d0e{suffix}",
        "platform": "python",
        "level": "error",
        "logger": "checkout.gateway",
        "release": "checkout@2026.8.3",
        "environment": "production",
        "server_name": "checkout-6d4b9f7c8-2xkqn",
        "transaction": "POST /api/checkout/authorise",
        "exception": {
            "values": [
                {
                    "type": "ConnectionResetError",
                    "module": "builtins",
                    "value": "[Errno 104] Connection reset by peer",
                    "mechanism": {"type": "chained", "handled": True},
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "urllib3/connectionpool.py",
                                "abs_path": "/usr/lib/python3.12/urllib3/connectionpool.py",
                                "module": "urllib3.connectionpool",
                                "function": "_make_request",
                                "lineno": 468,
                                "in_app": False,
                            },
                            {
                                "filename": "http/client.py",
                                "abs_path": "/usr/lib/python3.12/http/client.py",
                                "module": "http.client",
                                "function": "getresponse",
                                "lineno": 1428,
                                "in_app": False,
                            },
                        ]
                    },
                },
                {
                    "type": "PaymentGatewayError",
                    "module": "checkout.errors",
                    "value": "acquirer refused the authorisation attempt",
                    "mechanism": {"type": "django", "handled": False},
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "django/core/handlers/base.py",
                                "abs_path": "/srv/venv/django/core/handlers/base.py",
                                "module": "django.core.handlers.base",
                                "function": "_get_response",
                                "lineno": 197,
                                "in_app": False,
                            },
                            {
                                "filename": "checkout/views.py",
                                "abs_path": "/srv/app/checkout/views.py",
                                "module": "checkout.views",
                                "function": "authorise",
                                "lineno": 88,
                                "in_app": True,
                                "pre_context": [
                                    "def authorise(request):",
                                    "    basket = Basket.load(request)",
                                    "    card = request.data['card']",
                                ],
                                "context_line": "    return gateway.charge(card, basket.total)",
                                "post_context": [
                                    "",
                                    "",
                                    "def refund(request, order_id):",
                                ],
                                "vars": {
                                    "basket.total": "48250",
                                    "request.method": "POST",
                                },
                            },
                            {
                                "filename": "checkout/gateway.py",
                                "abs_path": "/srv/app/checkout/gateway.py",
                                "module": "checkout.gateway",
                                "function": "charge",
                                "lineno": 141,
                                "in_app": True,
                                "pre_context": [
                                    "    response = self.session.post(",
                                    "        self.endpoint,",
                                    "        json=body,",
                                    "        timeout=self.timeout,",
                                    "    )",
                                ],
                                "context_line": "    raise PaymentGatewayError(response.json()['reason'])",
                                "post_context": [
                                    "",
                                    "def refund(self, charge_id):",
                                ],
                                "vars": {
                                    "self.endpoint": "https://acquirer.invalid/v2/authorise",
                                    "self.timeout": "3.0",
                                    "amount": "48250",
                                    "attempt": "3",
                                },
                            },
                        ]
                    },
                },
            ]
        },
        "breadcrumbs": {
            "values": [
                {
                    "type": "http",
                    "category": "httplib",
                    "level": "info",
                    "message": "POST https://acquirer.invalid/v2/authorise",
                    "data": {"status_code": 502, "reason": "Bad Gateway"},
                },
                {
                    "type": "default",
                    "category": "checkout",
                    "level": "warning",
                    "message": "retrying authorisation, attempt 3 of 3",
                },
                {
                    "type": "query",
                    "category": "db",
                    "level": "info",
                    "message": "SELECT ... FROM checkout_basket WHERE id = %s",
                    "data": {"duration_ms": 4},
                },
            ]
        },
        "user": {
            "id": "44182",
            "username": "renata.k",
            "email": "renata@example.invalid",
            "ip_address": "203.0.113.44",
        },
        "request": {
            "url": "https://shop.example.invalid/api/checkout/authorise",
            "method": "POST",
            "query_string": "",
            "headers": {
                "User-Agent": "shop-web/4.2.1",
                "Content-Type": "application/json",
            },
        },
        "contexts": {
            "runtime": {"name": "CPython", "version": "3.12.7"},
            "os": {"name": "Linux", "version": "6.8.0"},
            "trace": {"trace_id": transaction_id, "op": "http.server"},
        },
        "tags": {
            "handler": "authorise",
            "acquirer": "northbank",
            "namespace": "storefront",
            "cluster": "p-mk2",
        },
        "extra": {"basket_id": "b-91f2c", "attempts": 3},
        "sdk": {"name": "sentry.python.django", "version": "2.24.1"},
    }


def _browser_event(suffix: str) -> dict[str, Any]:
    return {
        "event_id": f"a83b41cd6e2f47b0925c{suffix}",
        "platform": "javascript",
        "level": "error",
        "release": "storefront@2026.8.3",
        "environment": "production",
        "transaction": "/basket",
        "exception": {
            "values": [
                {
                    "type": "TypeError",
                    "value": "Cannot read properties of undefined (reading 'total')",
                    "mechanism": {"type": "onerror", "handled": False},
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "app://vendor.7f21c4.js",
                                "function": "dispatch",
                                "lineno": 1,
                                "colno": 88214,
                                "in_app": False,
                            },
                            {
                                "filename": "app://basket.4c9e10.js",
                                "function": "renderSummary",
                                "lineno": 1,
                                "colno": 20488,
                                "in_app": True,
                            },
                        ]
                    },
                }
            ]
        },
        "breadcrumbs": {
            "values": [
                {
                    "type": "navigation",
                    "category": "navigation",
                    "level": "info",
                    "message": "/products -> /basket",
                },
                {
                    "type": "http",
                    "category": "fetch",
                    "level": "error",
                    "message": "GET /api/basket",
                    "data": {"status_code": 500},
                },
                {
                    "type": "ui",
                    "category": "ui.click",
                    "level": "info",
                    "message": "button#checkout",
                },
            ]
        },
        "user": {"id": "44182", "ip_address": "203.0.113.44"},
        "request": {
            "url": "https://shop.example.invalid/basket",
            "method": "GET",
            "headers": {"User-Agent": "Mozilla/5.0 (Macintosh) Firefox/141.0"},
        },
        "contexts": {
            "browser": {"name": "Firefox", "version": "141.0"},
            "os": {"name": "Mac OS X", "version": "15.6"},
        },
        "tags": {"route": "/basket", "namespace": "storefront", "cluster": "p-mk2"},
        "sdk": {"name": "sentry.javascript.browser", "version": "9.12.0"},
    }


CHECKOUT_MINUTES = (9, 14, 21, 23, 26, 38, 47, 74)


def _checkout_events() -> tuple[DemoEvent, ...]:
    return tuple(
        DemoEvent(
            minutes_ago=minutes,
            payload=_python_event(f"a{index}", f"7c1e4d9a2b6f4e{index:02d}"),
        )
        for index, minutes in enumerate(CHECKOUT_MINUTES)
    )


EVENTS = (*_checkout_events(), DemoEvent(minutes_ago=23, payload=_browser_event("b1")))


def seed(project: Project, environment: str, now: datetime) -> int:
    seeded = 0
    for event in EVENTS:
        received_at = now - timedelta(minutes=event.minutes_ago)
        payload = dict(event.payload)
        payload["timestamp"] = received_at.isoformat()
        envelope = RawEnvelope.objects.create(
            project=project,
            source=TokenSource.SDK,
            environment=environment,
            payload=payload,
            received_at=received_at,
        )
        process_envelope(envelope.pk)
        seeded += 1
    return seeded
