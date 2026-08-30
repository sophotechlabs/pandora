import pytest
from django import db

from pandora.core import models as core_models
from pandora.ingest import models


@pytest.fixture
def envelope(project, am_fixture):
    return models.RawEnvelope.objects.create(
        project=project,
        source=core_models.TokenSource.AM,
        environment="p-mk1",
        payload=am_fixture("firing_group"),
    )


# field contract


def test_envelope_states_cover_the_inbox_lifecycle():
    """Should offer pending, claimed, done and failed — claimed is the consumer's."""
    result = list(models.EnvelopeState.values)
    expected = ["pending", "claimed", "done", "failed"]

    assert result == expected


def test_envelope_starts_pending_with_no_error(envelope):
    """Should land in the inbox unprocessed and unblamed."""
    result = {"state": envelope.state, "error": envelope.error}
    expected = {"state": "pending", "error": ""}

    assert result == expected


def test_envelope_records_the_delivering_environment(envelope):
    """Should keep the token's environment on the row so replay is deterministic."""
    envelope.refresh_from_db()

    result = envelope.environment
    expected = "p-mk1"

    assert result == expected


# payload tests


def test_envelope_payload_survives_a_round_trip(envelope, am_fixture):
    """Should store the webhook body verbatim for replay."""
    envelope.refresh_from_db()

    result = envelope.payload
    expected = am_fixture("firing_group")

    assert result == expected


def test_envelope_payload_keeps_nested_alert_labels(envelope):
    """Should preserve nested JSON structure, not a flattened string."""
    envelope.refresh_from_db()

    result = envelope.payload["alerts"][0]["labels"]["pod"]
    expected = "ledger-7d9f4c8b6d-hk2mp"

    assert result == expected


# dedup marker tests


@pytest.mark.django_db
def test_processed_event_is_unique_per_project(project):
    """Should refuse a second marker for an event id already seen."""
    models.ProcessedEvent.objects.create(project=project, event_id="abc")

    with pytest.raises(db.IntegrityError):
        models.ProcessedEvent.objects.create(project=project, event_id="abc")


@pytest.mark.django_db
def test_processed_event_ids_are_scoped_to_one_project(project):
    """Should allow the same event id under a different project."""
    other = core_models.Project.objects.create(slug="apps", name="Applications")
    models.ProcessedEvent.objects.create(project=project, event_id="abc")

    marker = models.ProcessedEvent.objects.create(project=other, event_id="abc")

    assert marker.pk is not None


# display


def test_envelope_is_shown_by_source_and_state(envelope):
    """Should render an envelope as source, id and state."""
    result = str(envelope)
    expected = f"am envelope {envelope.pk} (pending)"

    assert result == expected


@pytest.mark.django_db
def test_processed_event_shows_its_event_id(project):
    """Should render a dedup marker as the event id it guards."""
    marker = models.ProcessedEvent.objects.create(project=project, event_id="abc")

    result = str(marker)
    expected = "abc"

    assert result == expected
