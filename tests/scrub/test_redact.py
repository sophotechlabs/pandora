import io

import pytest
from django.core import management
from django.core.management.base import CommandError
from django.utils import timezone

from pandora.core import models as core_models
from pandora.events import types
from pandora.events.store import get_store
from pandora.scrub.models import ScrubRule
from pandora.scrub.rules import REDACTED

pytestmark = pytest.mark.django_db


def store_event(project, index=1, **overrides):
    fields = {
        "id": f"01J8ZQ7X4N{index:022d}",
        "project_id": project.pk,
        "timestamp": timezone.now(),
        "level": "error",
        "message": "declined 4111111111111111",
        "source": "sdk",
        "environment": "p-mk1",
        "tags": {"api_key": "sk-live-1"},
        "extra": {"note": "card 4111111111111111"},
        "payload": {"user": {"password": "hunter2", "email": "a@b.test"}},
    }
    fields.update(overrides)
    event = types.Event(**fields)
    get_store().insert([event])
    return event


def run(*args):
    out = io.StringIO()
    management.call_command("redact", *args, stdout=out)
    return out.getvalue()


def stored(project):
    return get_store().fetch(project.pk, limit=100)


# what it rewrites


def test_a_stored_secret_is_redacted(project):
    """Should be the only fix when the leak came from an app version you cannot patch."""
    store_event(project)

    run()

    result = stored(project)[0].payload["user"]["password"]
    expected = REDACTED

    assert result == expected


def test_a_stored_card_is_masked_everywhere_it_appears(project):
    """Should reach the message and the extra bag, not only the structured payload."""
    store_event(project)

    run()

    event = stored(project)[0]

    result = (event.message, event.extra["note"], event.tags["api_key"])
    expected = (f"declined {REDACTED}", f"card {REDACTED}", REDACTED)

    assert result == expected


def test_a_configured_rule_reaches_stored_events(project):
    """Should apply a rule written after the fact, which is the point of running it at all."""
    store_event(project)
    ScrubRule.objects.create(name="email", path="user.email")

    run()

    result = stored(project)[0].payload["user"]["email"]
    expected = REDACTED

    assert result == expected


def test_a_clean_event_is_not_rewritten(project):
    """Should not churn every row on every run — only what actually changes is written."""
    store_event(project, message="all fine", tags={}, extra={}, payload={})

    output = run()

    result = "1 scanned, 0 rewritten" in output

    assert result is True


def test_the_report_counts_what_it_touched(project):
    """Should say how much of the store it rewrote."""
    store_event(project, index=1)
    store_event(project, index=2)

    output = run()

    result = "2 scanned, 2 rewritten" in output

    assert result is True


# scope and safety


def test_one_project_can_be_redacted_alone(project):
    """Should let an operator fix one leak without rewriting the whole install."""
    other = core_models.Project.objects.create(slug="apps", name="Applications")
    store_event(project, index=1)
    store_event(other, index=2)

    run("--project", "infrastructure")

    result = (
        stored(project)[0].payload["user"]["password"],
        stored(other)[0].payload["user"]["password"],
    )
    expected = (REDACTED, "hunter2")

    assert result == expected


def test_an_unknown_project_is_an_error(project):
    """Should catch a typo rather than silently rewriting nothing."""
    with pytest.raises(CommandError, match="no project with slug 'typo'"):
        run("--project", "typo")


def test_a_dry_run_changes_nothing(project):
    """Should let an operator see the scale before a rewrite touches a live store."""
    store_event(project)

    output = run("--dry-run")

    result = (
        stored(project)[0].payload["user"]["password"],
        "rolled back" in output,
    )
    expected = ("hunter2", True)

    assert result == expected


def test_a_batch_below_one_is_an_error(project):
    """Should not loop forever on a nonsense page size."""
    with pytest.raises(CommandError, match="--batch must be at least 1"):
        run("--batch", "0")


def test_it_pages_through_more_events_than_one_batch(project):
    """Should finish a store larger than a single page, which is the case that matters."""
    for index in range(1, 6):
        store_event(project, index=index)

    output = run("--batch", "2")

    result = "5 scanned, 5 rewritten" in output

    assert result is True


def test_an_empty_store_is_not_an_error(project):
    """Should be safe to run on an install with nothing in it yet."""
    output = run()

    result = "0 scanned, 0 rewritten" in output

    assert result is True
