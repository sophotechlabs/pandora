import datetime

import pytest

from pandora.issues import correlate, models

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture
def build_issue(project):
    def build(title, labels=None, tags=None, first_seen=None, **overrides):
        moment = first_seen or NOW - datetime.timedelta(hours=1)
        issue = models.Issue.objects.create(
            project=project,
            fingerprint_hash=title,
            fingerprint=[title],
            grouping_labels=labels or {},
            title=title,
            culprit=title,
            level=models.Level.ERROR,
            environment="p-mk1",
            first_seen=moment,
            last_seen=NOW,
            **overrides,
        )
        for key, value in (tags or {}).items():
            models.TagStat.objects.create(issue=issue, key=key, value=value, count=1)
        return issue

    return build


def stat(issue, hour, count):
    models.HourlyStat.objects.create(issue=issue, hour=hour, count=count)


def episode(issue, starts_at, ends_at=None):
    return models.Episode.objects.create(
        project=issue.project,
        issue=issue,
        am_fingerprint=f"{issue.pk}-{starts_at:%H%M}",
        labels=dict(issue.grouping_labels),
        environment=issue.environment,
        starts_at=starts_at,
        ends_at=ends_at,
    )


# the window


def test_an_open_episode_makes_the_window_run_to_now(build_issue):
    """Should treat a firing alert as still happening rather than closing at its last delivery."""
    issue = build_issue("alert", labels={"namespace": "payments"})
    episode(issue, NOW - datetime.timedelta(hours=2))

    result = correlate.window(issue, NOW)
    expected = (NOW - datetime.timedelta(hours=2), NOW)

    assert result == expected


def test_a_closed_episode_bounds_the_window_at_its_end(build_issue):
    """Should ask what else moved while the alert was firing, not since."""
    issue = build_issue("alert", labels={"namespace": "payments"})
    start = NOW - datetime.timedelta(hours=4)
    end = NOW - datetime.timedelta(hours=3)
    episode(issue, start, end)

    result = correlate.window(issue, NOW)
    expected = (start, end)

    assert result == expected


def test_an_issue_without_episodes_gets_a_margin_around_first_seen(build_issue):
    """Should give an SDK issue, which has no lifecycle, a window to look in."""
    seen = NOW - datetime.timedelta(hours=5)
    issue = build_issue("sdk", tags={"namespace": "payments"}, first_seen=seen)

    result = correlate.window(issue, NOW)
    expected = (
        seen - datetime.timedelta(minutes=60),
        seen + datetime.timedelta(minutes=60),
    )

    assert result == expected


# the label join


def test_labels_come_from_grouping_and_from_tags(build_issue):
    """Should read both, because an alert carries grouping labels and an SDK event carries tags."""
    issue = build_issue(
        "alert",
        labels={"namespace": "payments"},
        tags={"pod": "ledger-1", "severity": "critical"},
    )

    result = correlate.labels(issue, correlate.DEFAULT_KEYS)
    expected = {"namespace": {"payments"}, "pod": {"ledger-1"}}

    assert result == expected


def test_server_name_is_read_as_a_pod(build_issue):
    """Should join an SDK event to an alert — the SDK reports server_name where the alert says pod."""
    issue = build_issue("sdk", tags={"server_name": "ledger-1"})

    result = correlate.labels(issue, correlate.DEFAULT_KEYS)
    expected = {"pod": {"ledger-1"}}

    assert result == expected


def test_a_key_outside_the_set_is_ignored(build_issue):
    """Should join on infrastructure identity, not on whatever an application happened to tag."""
    issue = build_issue("sdk", tags={"handler": "authorise"})

    result = correlate.labels(issue, correlate.DEFAULT_KEYS)
    expected = {}

    assert result == expected


def test_shared_reports_every_matching_pair():
    """Should name the evidence, because a join on one weak key should look weak."""
    left = {"namespace": {"payments"}, "pod": {"a", "b"}}
    right = {"namespace": {"payments"}, "pod": {"b"}, "node": {"n1"}}

    result = correlate.shared(left, right)
    expected = (("namespace", "payments"), ("pod", "b"))

    assert result == expected


def test_shared_is_empty_when_nothing_overlaps():
    """Should refuse to correlate two issues that have no infrastructure in common."""
    result = correlate.shared({"namespace": {"a"}}, {"namespace": {"b"}})
    expected = ()

    assert result == expected


# what comes back


def test_an_issue_spiking_in_the_window_and_sharing_a_label_is_matched(build_issue):
    """Should surface the whole point: what else went wrong here while this was firing."""
    alert = build_issue("alert", labels={"namespace": "payments"})
    episode(alert, NOW - datetime.timedelta(hours=2))
    other = build_issue("sdk", tags={"namespace": "payments"})
    stat(other, NOW - datetime.timedelta(hours=1), 40)

    result = [match.issue.pk for match in correlate.build(alert, NOW).matches]
    expected = [other.pk]

    assert result == expected


def test_an_issue_that_shares_nothing_is_left_out(build_issue):
    """Should not list every noisy issue on the box just because it was busy."""
    alert = build_issue("alert", labels={"namespace": "payments"})
    episode(alert, NOW - datetime.timedelta(hours=2))
    other = build_issue("sdk", tags={"namespace": "storefront"})
    stat(other, NOW - datetime.timedelta(hours=1), 40)

    result = correlate.build(alert, NOW).matches
    expected = ()

    assert result == expected


def test_an_issue_at_its_usual_rate_is_left_out(build_issue):
    """Should rank by rate change, not volume — the chatty issue is always busy and never the answer."""
    alert = build_issue("alert", labels={"namespace": "payments"})
    episode(alert, NOW - datetime.timedelta(hours=2))
    steady = build_issue("sdk", tags={"namespace": "payments"})
    for offset in range(1, 24 * 7):
        stat(steady, NOW - datetime.timedelta(hours=offset), 10)

    result = correlate.build(alert, NOW).matches
    expected = ()

    assert result == expected


def test_the_issue_itself_is_never_its_own_match(build_issue):
    """Should not list the issue you are already reading."""
    alert = build_issue("alert", labels={"namespace": "payments"})
    episode(alert, NOW - datetime.timedelta(hours=2))
    stat(alert, NOW - datetime.timedelta(hours=1), 40)

    result = correlate.build(alert, NOW).matches
    expected = ()

    assert result == expected


def test_matches_are_ordered_by_how_far_each_rose(build_issue):
    """Should put the biggest departure from normal first."""
    alert = build_issue("alert", labels={"namespace": "payments"})
    episode(alert, NOW - datetime.timedelta(hours=2))
    small = build_issue("small", tags={"namespace": "payments"})
    stat(small, NOW - datetime.timedelta(hours=1), 5)
    large = build_issue("large", tags={"namespace": "payments"})
    stat(large, NOW - datetime.timedelta(hours=1), 60)

    result = [match.issue.title for match in correlate.build(alert, NOW).matches]
    expected = ["large", "small"]

    assert result == expected


def test_an_issue_with_no_shared_key_of_its_own_correlates_nothing(build_issue):
    """Should return early rather than scanning the project for an issue that cannot join."""
    alert = build_issue("alert", labels={"alertname": "Watchdog"})
    episode(alert, NOW - datetime.timedelta(hours=2))
    other = build_issue("sdk", tags={"namespace": "payments"})
    stat(other, NOW - datetime.timedelta(hours=1), 40)

    result = correlate.build(alert, NOW).matches
    expected = ()

    assert result == expected


def test_a_quiet_window_correlates_nothing(build_issue):
    """Should say nothing rather than reaching outside the window for something to show."""
    alert = build_issue("alert", labels={"namespace": "payments"})
    episode(
        alert,
        NOW - datetime.timedelta(days=3),
        NOW - datetime.timedelta(days=3, hours=-1),
    )
    other = build_issue("sdk", tags={"namespace": "payments"})
    stat(other, NOW - datetime.timedelta(hours=1), 40)

    result = correlate.build(alert, NOW).matches
    expected = ()

    assert result == expected


def test_the_result_carries_the_window_and_the_keys(build_issue):
    """Should let the page state what it joined on, so a weak join reads as weak."""
    alert = build_issue("alert", labels={"namespace": "payments"})
    episode(alert, NOW - datetime.timedelta(hours=2))

    result = correlate.build(alert, NOW)

    assert result.keys == correlate.DEFAULT_KEYS
    assert result.ends_at == NOW


# configuration


def test_the_key_set_is_configurable(settings, build_issue):
    """Should let an operator whose labels are named differently still get a join."""
    settings.PANDORA_CORRELATION_KEYS = "team, tier"

    result = correlate.keys()
    expected = ("team", "tier")

    assert result == expected


def test_an_empty_key_setting_falls_back_to_the_default(settings):
    """Should never leave the join with nothing to match on."""
    settings.PANDORA_CORRELATION_KEYS = "  "

    result = correlate.keys()
    expected = correlate.DEFAULT_KEYS

    assert result == expected


def test_a_blank_label_value_never_joins(build_issue):
    """Should not match two issues on both having an empty namespace."""
    issue = build_issue("alert", labels={"namespace": "", "pod": "ledger-1"})

    result = correlate.labels(issue, correlate.DEFAULT_KEYS)
    expected = {"pod": {"ledger-1"}}

    assert result == expected
