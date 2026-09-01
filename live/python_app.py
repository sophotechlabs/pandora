"""A real application, using the real Sentry SDK, pointed at pandora.

Nothing here knows it is a test. The point is that the wire format is the one
`sentry-sdk` produces, not one this repo wrote — the whole compatibility claim
rests on that difference.
"""

from __future__ import annotations

import os
import sys
import time

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

RELEASE = "1.4.2"
ENVIRONMENT = "live"


def charge(order: dict, rate: float) -> float:
    total = order["amount"] * rate
    discount = order["discount"]
    return total / discount


def take_payment() -> None:
    order = {"amount": 4200, "discount": 0, "currency": "EUR"}
    charge(order, 1.09)


def crash() -> None:
    order = {"amount": 1, "discount": 0, "currency": "EUR"}
    charge(order, 1.0)


def main() -> None:
    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        release=RELEASE,
        environment=ENVIRONMENT,
        send_default_pii=True,
        attach_stacktrace=True,
        include_local_variables=True,
        max_breadcrumbs=50,
        traces_sample_rate=0,
        integrations=[LoggingIntegration(level=None, event_level=None)],
    )

    sentry_sdk.start_session("application")

    if "crash" in sys.argv:
        sentry_sdk.set_tag("service", "nightly")
        crash()

    sentry_sdk.set_tag("service", "checkout")
    sentry_sdk.set_tag("namespace", "payments")
    sentry_sdk.set_tag("region", "eu-central")
    sentry_sdk.set_user({"id": "4211", "username": "live-operator"})
    sentry_sdk.set_context("order", {"id": "ord-9931", "items": 3})

    sentry_sdk.add_breadcrumb(
        category="auth", message="operator signed in", level="info"
    )
    sentry_sdk.add_breadcrumb(category="cart", message="added SKU-77", level="info")
    sentry_sdk.add_breadcrumb(
        category="http", message="POST /checkout 500", level="error"
    )

    try:
        take_payment()
    except ZeroDivisionError:
        event_id = sentry_sdk.capture_exception()
        print(f"captured exception {event_id}")

    sentry_sdk.capture_message("checkout queue is backing up", level="warning")

    with sentry_sdk.new_scope() as scope:
        scope.set_tag("service", "worker")
        try:
            {"a": 1}["missing"]
        except KeyError:
            sentry_sdk.capture_exception()

    sentry_sdk.end_session()
    sentry_sdk.flush(timeout=10)
    time.sleep(1)
    print("python client done")


if __name__ == "__main__":
    main()
