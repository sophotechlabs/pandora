import pytest

from pandora.core import models as core_models
from pandora.issues import models as issue_models
from pandora.notify import events, models
from pandora.notify.models import Delivery

pytestmark = pytest.mark.django_db


# milestones


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [(9, 10, 10), (10, 11, None), (99, 101, 100), (0, 1, None), (999, 1000, 1000)],
)
def test_a_milestone_fires_once_when_it_is_crossed(before, after, expected):
    """Should mark the tenth and the hundredth, not every occurrence after them."""
    result = events.milestone_reached(before, after)

    assert result == expected


# which destinations want an event


def test_a_destination_gets_the_events_it_asked_for(make_issue, make_destination):
    """Should not send a regression to somewhere that only wanted new issues."""
    make_destination(events=[models.NEW])
    issue = make_issue()

    result = [d.name for d in events.destinations_for(issue, models.REGRESSION)]
    expected = []

    assert result == expected


def test_a_disabled_destination_gets_nothing(make_issue, make_destination):
    """Should let a destination be turned off without deleting its configuration."""
    make_destination(enabled=False)
    issue = make_issue()

    result = events.destinations_for(issue, models.NEW)
    expected = []

    assert result == expected


def test_a_destination_scoped_to_another_project_gets_nothing(
    make_issue, make_destination
):
    """Should keep one team's alerts off another team's channel."""
    other = core_models.Project.objects.create(slug="apps", name="Applications")
    make_destination(project=other)
    issue = make_issue()

    result = events.destinations_for(issue, models.NEW)
    expected = []

    assert result == expected


def test_an_unscoped_destination_gets_every_project(make_issue, make_destination):
    """Should let one channel cover a small install, which is the common case."""
    make_destination(project=None)
    issue = make_issue()

    result = [d.name for d in events.destinations_for(issue, models.NEW)]
    expected = ["ops"]

    assert result == expected


def test_a_quieter_issue_than_the_threshold_is_skipped(make_issue, make_destination):
    """Should let a channel take errors without taking every debug line."""
    make_destination(min_level=issue_models.Level.ERROR)
    issue = make_issue(level=issue_models.Level.INFO)

    result = events.destinations_for(issue, models.NEW)
    expected = []

    assert result == expected


def test_a_louder_issue_than_the_threshold_is_sent(make_issue, make_destination):
    """Should pass anything at or above the level asked for."""
    make_destination(min_level=issue_models.Level.WARNING)
    issue = make_issue(level=issue_models.Level.FATAL)

    result = [d.name for d in events.destinations_for(issue, models.NEW)]
    expected = ["ops"]

    assert result == expected


# what gets queued


def test_queueing_writes_one_delivery_per_destination(make_issue, make_destination):
    """Should fan out to every channel that wants it, in one place, durably."""
    make_destination(name="first")
    make_destination(name="second")
    issue = make_issue()

    events.queue(issue, models.NEW)

    result = sorted(Delivery.objects.values_list("destination__name", flat=True))
    expected = ["first", "second"]

    assert result == expected


def test_queueing_with_no_destination_writes_nothing(make_issue):
    """Should cost nothing on an install that has configured no notifications."""
    issue = make_issue()

    events.queue(issue, models.NEW)

    result = Delivery.objects.count()
    expected = 0

    assert result == expected


def test_the_payload_names_the_issue_and_where_to_read_it(
    make_issue, make_destination, settings
):
    """Should carry a link, because a notification without one makes someone go looking."""
    settings.PANDORA_BASE_URL = "https://pandora.example.test"
    make_destination()
    issue = make_issue(title="PaymentGatewayError")

    events.queue(issue, models.NEW)
    payload = Delivery.objects.get().payload

    result = (payload["event"], payload["issue"]["title"], payload["issue"]["url"])
    expected = (
        models.NEW,
        "PaymentGatewayError",
        f"https://pandora.example.test/issues/{issue.pk}/",
    )

    assert result == expected


def test_the_url_falls_back_to_a_path_without_a_base(
    make_issue, make_destination, settings
):
    """Should still say which issue when nobody set the public address."""
    settings.PANDORA_BASE_URL = ""
    make_destination()
    issue = make_issue()

    events.queue(issue, models.NEW)

    result = Delivery.objects.get().payload["issue"]["url"]
    expected = f"/issues/{issue.pk}/"

    assert result == expected


def test_extra_fields_reach_the_payload(make_issue, make_destination):
    """Should let a milestone say which one it was."""
    make_destination(events=[models.MILESTONE])
    issue = make_issue()

    events.queue(issue, models.MILESTONE, {"milestone": 100})

    result = Delivery.objects.get().payload["milestone"]
    expected = 100

    assert result == expected


def test_the_payload_names_the_owning_team(make_issue, make_destination):
    """Should let the receiving chat channel route without a second lookup."""
    from pandora.people.models import Assignment, Team

    make_destination()
    issue = make_issue()
    Assignment.objects.create(issue=issue, team=Team.objects.create(name="payments"))

    events.queue(issue, models.NEW)

    result = Delivery.objects.get().payload["owner"]
    expected = {"team": "payments", "user": None}

    assert result == expected


def test_the_payload_names_the_owning_person(make_issue, make_destination):
    """Should name whoever a rule routed it to, team or not."""
    from django.contrib.auth import models as auth_models

    from pandora.people.models import Assignment

    make_destination()
    issue = make_issue()
    Assignment.objects.create(
        issue=issue,
        user=auth_models.User.objects.create_user(username="dev", password="x"),
    )

    events.queue(issue, models.NEW)

    result = Delivery.objects.get().payload["owner"]
    expected = {"team": None, "user": "dev"}

    assert result == expected


def test_an_unowned_issue_carries_no_owner(make_issue, make_destination):
    """Should say nobody owns it rather than inventing a team name."""
    make_destination()

    events.queue(make_issue(), models.NEW)

    result = Delivery.objects.get().payload["owner"]

    assert result is None
