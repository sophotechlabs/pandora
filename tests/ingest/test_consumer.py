import datetime
import io

import pytest
from django.core import management
from django.utils import timezone

from pandora.core import models as core_models
from pandora.ingest import consumer
from pandora.ingest import models as ingest_models
from pandora.ingest.queue import AsyncQueue, SyncQueue
from pandora.issues import models as issue_models
from tests.ingest import fakes

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def payload(event_id="1" * 32):
    return {
        "event_id": event_id,
        "level": "error",
        "platform": "python",
        "exception": {"values": [{"type": "ValueError", "value": "bad"}]},
    }


@pytest.fixture
def pending(project):
    def build(event_id="1" * 32):
        return ingest_models.RawEnvelope.objects.create(
            project=project,
            source=core_models.TokenSource.SDK,
            payload=payload(event_id),
        )

    return build


# the queue seam


def test_the_default_queue_processes_inline(project, pending):
    """Should be unchanged — one container, no worker, and that is correct."""
    envelope = pending()

    SyncQueue().publish(envelope.pk)

    result = ingest_models.RawEnvelope.objects.get().state
    expected = ingest_models.EnvelopeState.DONE

    assert result == expected


def test_the_async_queue_leaves_it_pending(project, pending):
    """Should hand the work to the consumer instead of holding the request open."""
    envelope = pending()

    AsyncQueue().publish(envelope.pk)

    result = ingest_models.RawEnvelope.objects.get().state
    expected = ingest_models.EnvelopeState.PENDING

    assert result == expected


# claiming


def test_claiming_takes_the_pending_envelopes(project, pending):
    """Should be the batch this consumer owns for the rest of the pass."""
    first = pending("1" * 32)
    second = pending("2" * 32)

    result = sorted(consumer.claim())
    expected = sorted([first.pk, second.pk])

    assert result == expected


def test_a_claimed_envelope_is_not_claimed_twice(project, pending):
    """Should be why two consumers can run against one database."""
    pending()
    consumer.claim()

    result = consumer.claim()
    expected = []

    assert result == expected


def test_claiming_is_bounded_by_the_batch(project, pending):
    """Should take a batch rather than the whole backlog in one transaction."""
    for index in range(5):
        pending(f"{index}" * 32)

    result = len(consumer.claim(batch=2))
    expected = 2

    assert result == expected


def test_the_oldest_envelope_is_claimed_first(project, pending):
    """Should drain in the order things arrived."""
    older = pending("1" * 32)
    ingest_models.RawEnvelope.objects.filter(pk=older.pk).update(
        received_at=NOW - datetime.timedelta(hours=2)
    )
    pending("2" * 32)

    result = consumer.claim(batch=1)
    expected = [older.pk]

    assert result == expected


# draining


def test_a_pass_applies_what_it_claimed(project, pending):
    """Should turn the envelope into an issue like the inline path does."""
    pending()

    report = consumer.run_once(store=fakes.RecordingEventStore())

    result = (report.claimed, report.done, issue_models.Issue.objects.count())
    expected = (1, 1, 1)

    assert result == expected


class _BrokenStore(fakes.RecordingEventStore):
    def insert(self, events):
        raise RuntimeError("the store is down")


def test_a_failing_envelope_is_counted_and_stays_replayable(project, pending):
    """Should not lose an envelope the processor could not apply."""
    pending()

    report = consumer.run_once(store=_BrokenStore())

    result = (report.failed, ingest_models.RawEnvelope.objects.get().state)
    expected = (1, ingest_models.EnvelopeState.FAILED)

    assert result == expected


def test_an_empty_inbox_is_an_empty_pass(project):
    """Should do nothing rather than raise on a quiet install."""
    report = consumer.run_once()

    result = (report.claimed, report.done, report.failed)
    expected = (0, 0, 0)

    assert result == expected


def test_the_pass_reads_as_a_line(project, pending):
    """Should say what it did in one line a human can scan in a log."""
    pending()

    result = consumer.run_once(store=fakes.RecordingEventStore()).line()

    assert "1 claimed" in result and "1 applied" in result


# a consumer that died


def test_a_stale_claim_goes_back_to_pending(project, pending):
    """Should be the guarantee the durable table exists for."""
    envelope = pending()
    consumer.claim()
    ingest_models.RawEnvelope.objects.filter(pk=envelope.pk).update(
        received_at=NOW - datetime.timedelta(hours=2)
    )

    consumer.reclaim_stale(NOW - datetime.timedelta(minutes=15))

    result = ingest_models.RawEnvelope.objects.get().state
    expected = ingest_models.EnvelopeState.PENDING

    assert result == expected


def test_a_fresh_claim_is_left_alone(project, pending):
    """Should not steal a batch a live consumer is still working through."""
    pending()
    consumer.claim()

    consumer.reclaim_stale(NOW - datetime.timedelta(minutes=15))

    result = ingest_models.RawEnvelope.objects.get().state
    expected = ingest_models.EnvelopeState.CLAIMED

    assert result == expected


def test_releasing_puts_a_batch_back(project, pending):
    """Should let a consumer hand back what it will not finish."""
    envelope = pending()
    claimed = consumer.claim()

    result = consumer.release(claimed)

    assert result == 1
    assert ingest_models.RawEnvelope.objects.get(pk=envelope.pk).state == (
        ingest_models.EnvelopeState.PENDING
    )


# the command


def test_the_command_drains_once(project, pending, settings):
    """Should be runnable as a cron job as well as a loop."""
    settings.PANDORA_QUEUE = "pandora.ingest.queue.AsyncQueue"
    pending()
    out = io.StringIO()

    management.call_command("consume", stdout=out)

    assert "1 applied" in out.getvalue()


def test_the_command_reports_what_it_put_back(project, pending):
    """Should say when it recovered someone else's abandoned batch."""
    envelope = pending()
    consumer.claim()
    ingest_models.RawEnvelope.objects.filter(pk=envelope.pk).update(
        received_at=NOW - datetime.timedelta(hours=2)
    )
    out = io.StringIO()

    management.call_command("consume", stdout=out)

    assert "put back 1 stale claim" in out.getvalue()


def test_the_loop_can_be_stopped(project, pending, monkeypatch, settings):
    """Should keep running until something stops it, like the reconcile loop."""
    settings.PANDORA_QUEUE = "pandora.ingest.queue.AsyncQueue"
    pending()
    calls = {"count": 0}

    def stop(_seconds):
        calls["count"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", stop)

    with pytest.raises(KeyboardInterrupt):
        management.call_command("consume", loop=1, stdout=io.StringIO())

    assert calls["count"] == 1


def test_a_database_error_ends_the_process(project, monkeypatch):
    """Should die rather than swallow it — the restart brings a working connection."""
    from django import db

    from pandora.ingest import consumer as consumer_module

    def broken(*args, **kwargs):
        raise db.OperationalError("gone")

    monkeypatch.setattr(consumer_module, "run_once", broken)

    with pytest.raises(db.Error):
        management.call_command("consume", stdout=io.StringIO())
