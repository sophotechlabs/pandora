import pytest
from django import test

from pandora.ingest import models as ingest_models
from pandora.ingest import queue
from tests.ingest import helpers

# configuration


@test.override_settings(PANDORA_QUEUE="pandora.ingest.queue.SyncQueue")
def test_queue_factory_builds_the_configured_queue():
    """Should build the queue named by PANDORA_QUEUE."""
    result = queue.get_queue()

    assert isinstance(result, queue.SyncQueue)


# publish tests


@pytest.mark.django_db
def test_sync_queue_runs_the_consumer_inline(token, am_fixture):
    """Should call process_envelope in the caller's stack, not defer it."""
    envelope = helpers.store_envelope(am_fixture("firing_group"), token)

    queue.SyncQueue().publish(envelope.pk)
    envelope.refresh_from_db()

    result = envelope.state
    expected = ingest_models.EnvelopeState.PENDING

    assert result != expected


@pytest.mark.django_db
def test_sync_queue_leaves_a_broken_payload_replayable(token):
    """Should hand a failure back as a stored row, never as an exception."""
    envelope = helpers.store_envelope({"version": "5", "alerts": []}, token)

    queue.SyncQueue().publish(envelope.pk)
    envelope.refresh_from_db()

    result = envelope.state
    expected = ingest_models.EnvelopeState.FAILED

    assert result == expected
