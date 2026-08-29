import datetime

import pytest
from django.utils import timezone

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.ingest import processor
from pandora.issues import actions
from pandora.issues import models as issue_models
from pandora.notify import models
from pandora.notify.models import Delivery
from tests.ingest import fakes

pytestmark = pytest.mark.django_db


def sdk_payload(**overrides):
    payload = {
        "event_id": "b" * 32,
        "level": "error",
        "platform": "python",
        "exception": {"values": [{"type": "ValueError", "value": "bad input"}]},
    }
    payload.update(overrides)
    return payload


def deliver_event(project, payload=None):
    envelope = ingest_models.RawEnvelope.objects.create(
        project=project,
        source=core_models.TokenSource.SDK,
        payload=payload or sdk_payload(),
    )
    processor.process_envelope(envelope.pk, store=fakes.RecordingEventStore())


def queued_events():
    return sorted(Delivery.objects.values_list("event", flat=True))


# what ingest fires


def test_a_new_issue_queues_a_notification(project, make_destination):
    """Should be the first thing anyone wires up — something broke that was not broken before."""
    make_destination()

    deliver_event(project)

    result = queued_events()
    expected = [models.NEW]

    assert result == expected


def test_a_second_occurrence_queues_nothing(project, make_destination):
    """Should never notify per event — that is the behaviour that gets a tool muted."""
    make_destination()
    deliver_event(project)
    Delivery.objects.all().delete()

    deliver_event(project, sdk_payload(event_id="c" * 32))

    result = Delivery.objects.count()
    expected = 0

    assert result == expected


def test_a_regression_queues_a_notification(project, make_destination):
    """Should say when something you fixed came back, which is the second thing anyone wires up."""
    make_destination(events=[models.REGRESSION])
    deliver_event(project)
    issue = issue_models.Issue.objects.get()
    issue.triage_state = issue_models.TriageState.RESOLVED
    issue.last_resolved_at = timezone.now() - datetime.timedelta(hours=1)
    issue.save(update_fields=["triage_state", "last_resolved_at"])

    deliver_event(project, sdk_payload(event_id="c" * 32))

    result = queued_events()
    expected = [models.REGRESSION]

    assert result == expected


def test_a_milestone_queues_a_notification(project, make_destination):
    """Should mark a slow burn crossing a round number, which no threshold rule catches well."""
    make_destination(events=[models.MILESTONE])
    deliver_event(project)
    issue = issue_models.Issue.objects.get()
    issue.event_count = 9
    issue.save(update_fields=["event_count"])

    deliver_event(project, sdk_payload(event_id="c" * 32))

    payload = Delivery.objects.get().payload

    result = (payload["event"], payload["milestone"])
    expected = (models.MILESTONE, 10)

    assert result == expected


def test_a_hook_that_cannot_be_imported_does_not_break_ingest(project, settings):
    """Should never let a typo in a setting lose every event the door receives."""
    settings.PANDORA_ISSUE_HOOKS = "pandora.notify.hooks.does_not_exist"

    deliver_event(project)

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_a_hook_that_raises_does_not_break_ingest(project, make_destination, mocker):
    """Should keep the event even when a destination lookup blows up mid-notification."""
    make_destination()
    mocker.patch(
        "pandora.notify.events.destinations_for", side_effect=RuntimeError("boom")
    )

    deliver_event(project)

    result = (issue_models.Issue.objects.count(), Delivery.objects.count())
    expected = (1, 0)

    assert result == expected


def test_an_unset_hook_list_fires_nothing(project, make_destination, settings):
    """Should let an operator turn notifications off entirely without uninstalling them."""
    settings.PANDORA_ISSUE_HOOKS = ""
    make_destination()

    deliver_event(project)

    result = Delivery.objects.count()
    expected = 0

    assert result == expected


# what triage fires


def test_waking_from_a_snooze_queues_a_notification(
    project, make_destination, make_issue
):
    """Should be the third transition worth knowing about — it went quiet and it is back."""
    make_destination(events=[models.UNSNOOZED])
    issue = make_issue()
    issue.snoozed_until = timezone.now() - datetime.timedelta(minutes=1)
    issue.save(update_fields=["snoozed_until"])

    actions.wake(issue, timezone.now())

    result = queued_events()
    expected = [models.UNSNOOZED]

    assert result == expected


def test_resolving_queues_a_notification(project, make_destination, make_issue):
    """Should let a channel see the close as well as the open."""
    make_destination(events=[models.RESOLVED])
    issue = make_issue()

    actions.apply_triage(issue, "resolved", "renata", timezone.now())

    payload = Delivery.objects.get().payload

    result = (payload["event"], payload["actor"])
    expected = (models.RESOLVED, "renata")

    assert result == expected


def test_acknowledging_queues_nothing(project, make_destination, make_issue):
    """Should not notify on every triage click."""
    make_destination(events=list(models.EVENTS))
    issue = make_issue()

    actions.apply_triage(issue, "ack", "renata", timezone.now())

    result = Delivery.objects.count()
    expected = 0

    assert result == expected
