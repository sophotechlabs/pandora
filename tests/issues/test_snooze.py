import datetime

import pytest

from pandora.issues import actions, models, snooze

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture
def make_issue(project):
    def build(**overrides):
        fields = {
            "project": project,
            "fingerprint_hash": overrides.pop("hash", "abc"),
            "fingerprint": ["a"],
            "title": "boom",
            "culprit": "boom",
            "level": models.Level.ERROR,
            "environment": "p-mk1",
            "first_seen": NOW - datetime.timedelta(hours=2),
            "last_seen": NOW,
            "event_count": 10,
        }
        fields.update(overrides)
        return models.Issue.objects.create(**fields)

    return build


# planning


def test_a_time_window_snoozes_until_a_moment(make_issue):
    """Should expire on its own, so the queue stays live rather than quietly emptying."""
    issue = make_issue()

    plan = snooze.plan(issue, "4h", NOW)

    result = (plan.until, plan.past_count)
    expected = (NOW + datetime.timedelta(hours=4), None)

    assert result == expected


def test_a_count_window_snoozes_past_a_number_of_occurrences(make_issue):
    """Should let an operator say 'be quiet for the next 500', which no Sentry equivalent offers."""
    issue = make_issue(event_count=42)

    plan = snooze.plan(issue, "500", NOW)

    result = (plan.until, plan.past_count)
    expected = (None, 542)

    assert result == expected


def test_an_unknown_window_is_refused(make_issue):
    """Should name what went wrong rather than snoozing forever by accident."""
    issue = make_issue()

    result = snooze.plan(issue, "forever", NOW).error
    expected = "forever is not a snooze window"

    assert result == expected


def test_there_is_no_indefinite_snooze():
    """Should refuse to hide an issue permanently — that is what resolve and ignore are for."""
    result = [
        spec for spec in snooze.WINDOWS if snooze.WINDOWS[spec] > snooze.MAX_WINDOW
    ]
    expected = []

    assert result == expected


# whether an issue is currently quiet


def test_an_issue_inside_its_window_is_snoozed(make_issue):
    """Should stay quiet until the moment passes."""
    issue = make_issue(snoozed_until=NOW + datetime.timedelta(hours=1))

    result = snooze.snoozed(issue, NOW)

    assert result is True


def test_an_issue_past_its_window_is_not(make_issue):
    """Should come back on its own, which is the whole difference from ignore."""
    issue = make_issue(snoozed_until=NOW - datetime.timedelta(hours=1))

    result = snooze.snoozed(issue, NOW)

    assert result is False


def test_an_issue_below_its_count_is_snoozed(make_issue):
    """Should stay quiet while the occurrences it was told to skip are still arriving."""
    issue = make_issue(event_count=10, snoozed_past_count=100)

    result = snooze.snoozed(issue, NOW)

    assert result is True


def test_an_issue_that_reached_its_count_is_not(make_issue):
    """Should speak up once the promised number has gone by."""
    issue = make_issue(event_count=100, snoozed_past_count=100)

    result = snooze.snoozed(issue, NOW)

    assert result is False


def test_an_issue_that_was_never_snoozed_has_not_expired(make_issue):
    """Should not fire a wake-up for an issue nobody silenced."""
    issue = make_issue()

    result = snooze.expired(issue, NOW)

    assert result is False


def test_a_finished_snooze_counts_as_expired(make_issue):
    """Should be detectable, because waking up is the event worth notifying on."""
    issue = make_issue(snoozed_until=NOW - datetime.timedelta(minutes=1))

    result = snooze.expired(issue, NOW)

    assert result is True


# applying it


def test_snoozing_records_what_was_asked_for(make_issue):
    """Should leave a trail — a silent issue with no reason is the thing nobody trusts."""
    issue = make_issue()

    report = actions.apply_snooze([issue], "1d", "renata", NOW)
    issue.refresh_from_db()
    activity = issue.activities.first()

    result = (report.snoozed, issue.snoozed_until, activity.kind, activity.data["spec"])
    expected = (1, NOW + datetime.timedelta(days=1), "snoozed", "1d")

    assert result == expected


def test_snoozing_many_issues_at_once(make_issue):
    """Should work from the selection bar, which is where a storm is triaged."""
    first = make_issue(hash="one")
    second = make_issue(hash="two")

    report = actions.apply_snooze([first, second], "1h", "renata", NOW)

    result = report.snoozed
    expected = 2

    assert result == expected


def test_a_bad_window_snoozes_nothing(make_issue):
    """Should refuse the whole batch rather than half-applying it."""
    issue = make_issue()

    report = actions.apply_snooze([issue], "nonsense", "renata", NOW)
    issue.refresh_from_db()

    result = (report.snoozed, report.errors, issue.snoozed_until)
    expected = (0, ("nonsense is not a snooze window",), None)

    assert result == expected


def test_waking_clears_both_fields(make_issue):
    """Should leave no half-state behind that would keep the issue quiet."""
    issue = make_issue(
        snoozed_until=NOW - datetime.timedelta(hours=1), snoozed_past_count=5
    )

    woke = actions.wake(issue, NOW)
    issue.refresh_from_db()

    result = (woke, issue.snoozed_until, issue.snoozed_past_count)
    expected = (True, None, None)

    assert result == expected


def test_waking_an_issue_still_snoozed_does_nothing(make_issue):
    """Should not cut a snooze short."""
    issue = make_issue(snoozed_until=NOW + datetime.timedelta(hours=1))

    result = actions.wake(issue, NOW)

    assert result is False


def test_waking_records_the_activity(make_issue):
    """Should mark the moment an issue came back, which is what a notification fires on."""
    issue = make_issue(snoozed_until=NOW - datetime.timedelta(hours=1))

    actions.wake(issue, NOW)

    result = issue.activities.first().kind
    expected = "unsnoozed"

    assert result == expected
