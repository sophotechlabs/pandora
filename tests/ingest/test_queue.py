import pytest
from django import test

from pandora.ingest import processor, queue

# configuration


@test.override_settings(PANDORA_QUEUE="pandora.ingest.queue.SyncQueue")
def test_queue_factory_builds_the_configured_queue():
    """Should build the queue named by PANDORA_QUEUE."""
    result = queue.get_queue()

    assert isinstance(result, queue.SyncQueue)


# publish tests


def test_sync_queue_runs_the_consumer_inline():
    """Should call process_envelope in the caller's stack, not defer it."""
    with pytest.raises(NotImplementedError) as error:
        queue.SyncQueue().publish(17)

    frames = [frame.name for frame in error.traceback]
    assert "process_envelope" in frames


def test_the_consumer_is_the_seam_phase_one_fills():
    """Should raise NotImplementedError until the Phase 1 consumer lands."""
    with pytest.raises(NotImplementedError):
        processor.process_envelope(1)
