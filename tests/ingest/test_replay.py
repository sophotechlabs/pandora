import io

import pytest
from django.core.management import call_command

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.ingest.management.commands import replay as replay_command
from tests.ingest import fakes, helpers

pytestmark = pytest.mark.django_db


def failed_envelope(token, payload=None, am_fixture=None):
    if payload is None:
        payload = am_fixture("firing_group")
    envelope = helpers.store_envelope(payload, token)
    envelope.state = ingest_models.EnvelopeState.FAILED
    envelope.error = "RuntimeError: event store is unreachable"
    envelope.save(update_fields=["state", "error"])
    return envelope


def run(**options):
    out = io.StringIO()
    call_command("replay", stdout=out, **options)
    return out.getvalue()


# the drain itself


def test_a_failed_envelope_is_reapplied(token, am_fixture):
    """Should give a lost alert a second chance — the inbox exists to be replayable."""
    envelope = failed_envelope(token, am_fixture=am_fixture)

    result = replay_command.replay(
        (ingest_models.EnvelopeState.FAILED,),
        10,
        store=fakes.RecordingEventStore(),
    )

    envelope.refresh_from_db()
    assert (result.attempted, result.done, result.failed) == (1, 1, 0)
    assert envelope.state == ingest_models.EnvelopeState.DONE


def test_a_replayed_envelope_clears_its_error(token, am_fixture):
    """Should not leave a stale error on a row that has since applied."""
    envelope = failed_envelope(token, am_fixture=am_fixture)

    replay_command.replay(
        (ingest_models.EnvelopeState.FAILED,), 10, store=fakes.RecordingEventStore()
    )

    envelope.refresh_from_db()
    result = envelope.error
    expected = ""
    assert result == expected


def test_a_pending_envelope_is_picked_up_too(token, am_fixture):
    """Should drain the ones the queue never got to, not only the failures."""
    helpers.store_envelope(am_fixture("firing_group"), token)

    result = replay_command.replay(
        (ingest_models.EnvelopeState.PENDING,),
        10,
        store=fakes.RecordingEventStore(),
    )

    assert (result.attempted, result.done) == (1, 1)


def test_a_done_envelope_is_never_touched(token, am_fixture):
    """Should leave applied envelopes alone so counters cannot double."""
    helpers.deliver(am_fixture("firing_group"), token, fakes.RecordingEventStore())

    result = replay_command.replay(
        (ingest_models.EnvelopeState.FAILED, ingest_models.EnvelopeState.PENDING),
        10,
        store=fakes.RecordingEventStore(),
    )

    assert result.attempted == 0


def test_replaying_twice_counts_the_alert_once(token, am_fixture):
    """Should stay exactly-once across a replay, which is the whole contract."""
    failed_envelope(token, am_fixture=am_fixture)
    states = (ingest_models.EnvelopeState.FAILED, ingest_models.EnvelopeState.PENDING)
    replay_command.replay(states, 10, store=fakes.RecordingEventStore())
    after_first = helpers.snapshot()

    second = replay_command.replay(states, 10, store=fakes.RecordingEventStore())

    assert second.attempted == 0
    assert helpers.snapshot() == after_first


def test_an_envelope_that_fails_again_stays_failed(token, am_fixture):
    """Should keep it replayable rather than swallow a second failure."""
    envelope = failed_envelope(token, am_fixture=am_fixture)

    result = replay_command.replay(
        (ingest_models.EnvelopeState.FAILED,), 10, store=fakes.FailingEventStore()
    )

    envelope.refresh_from_db()
    assert (result.done, result.failed) == (0, 1)
    assert envelope.state == ingest_models.EnvelopeState.FAILED


def test_the_limit_bounds_one_run(token, am_fixture):
    """Should not try to drain a huge backlog in a single pass."""
    for _ in range(3):
        failed_envelope(token, am_fixture=am_fixture)

    result = replay_command.replay(
        (ingest_models.EnvelopeState.FAILED,), 2, store=fakes.RecordingEventStore()
    )

    assert result.attempted == 2


def test_one_project_can_be_drained_alone(token, am_fixture, project):
    """Should let an operator replay a single noisy project."""
    other = core_models.Project.objects.create(slug="other", name="Other")
    other_token = core_models.IngestToken.objects.create(
        project=other,
        name="other",
        token="other-token",
        source=core_models.TokenSource.AM,
        scope=core_models.TokenScope.INGEST,
    )
    failed_envelope(token, am_fixture=am_fixture)
    failed_envelope(other_token, am_fixture=am_fixture)

    result = replay_command.replay(
        (ingest_models.EnvelopeState.FAILED,),
        10,
        project_slug="other",
        store=fakes.RecordingEventStore(),
    )

    assert result.attempted == 1


# the command surface


def test_the_command_reports_what_it_did(token, am_fixture):
    """Should tell the operator the outcome, not just exit zero."""
    failed_envelope(token, am_fixture=am_fixture)

    result = run(state="failed")

    assert "1 attempted" in result
    assert "1 done" in result


def test_a_dry_run_changes_nothing(token, am_fixture):
    """Should let an operator look before draining."""
    envelope = failed_envelope(token, am_fixture=am_fixture)

    result = run(dry_run=True)

    envelope.refresh_from_db()
    assert "would be replayed" in result
    assert str(envelope.pk) in result
    assert envelope.state == ingest_models.EnvelopeState.FAILED


def test_the_command_defaults_to_both_states(token, am_fixture):
    """Should drain failed and pending together unless told otherwise."""
    failed_envelope(token, am_fixture=am_fixture)
    helpers.store_envelope(am_fixture("resolved_group"), token)

    result = run()

    assert "2 attempted" in result
