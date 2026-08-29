import pytest

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.ingest import processor
from pandora.people import ownership
from pandora.people.models import Assignment, OwnershipRule
from tests.ingest import fakes

pytestmark = pytest.mark.django_db


def sdk_payload(**overrides):
    payload = {
        "event_id": "c" * 32,
        "level": "error",
        "platform": "python",
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad input",
                    "stacktrace": {
                        "frames": [{"filename": "src/payments/charge.py", "lineno": 4}]
                    },
                }
            ]
        },
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def deliver(project):
    store = fakes.RecordingEventStore()

    def send(payload=None):
        envelope = ingest_models.RawEnvelope.objects.create(
            project=project,
            source=core_models.TokenSource.SDK,
            payload=payload or sdk_payload(),
        )
        processor.process_envelope(envelope.pk, store=store)

    return send


@pytest.fixture
def path_rule(make_team):
    return OwnershipRule.objects.create(
        name="payments",
        pattern="src/payments/*",
        field=ownership.PATH,
        team=make_team("payments"),
    )


def test_a_new_issue_is_routed_to_the_owning_team(deliver, path_rule):
    """Should have an owner by the time a person opens it, not after triage."""
    deliver()

    result = Assignment.objects.get().team.name
    expected = "payments"

    assert result == expected


def test_an_issue_matching_nothing_is_left_unassigned(deliver, make_team):
    """Should not invent an owner when no rule claims the code."""
    OwnershipRule.objects.create(
        name="search", pattern="src/search/*", field=ownership.PATH, team=make_team()
    )

    deliver()

    assert Assignment.objects.count() == 0


def test_a_second_occurrence_does_not_reassign(deliver, path_rule, make_user):
    """Should not undo a hand-made reassignment on the next event."""
    deliver()
    Assignment.objects.update(team=None, user=make_user("dev"))

    deliver()

    result = Assignment.objects.get().user.username
    expected = "dev"

    assert result == expected


def test_an_install_with_no_rules_pays_no_query_cost(deliver):
    """Should stay out of the ingest path entirely until someone writes a rule."""
    deliver()

    assert Assignment.objects.count() == 0
