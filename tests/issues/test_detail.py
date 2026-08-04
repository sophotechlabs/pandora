import datetime

import freezegun
import pytest
from django.utils import timezone

from pandora.issues import detail, models

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def frozen():
    with freezegun.freeze_time("2026-08-04 12:00:00"):
        yield


@pytest.fixture
def now():
    return timezone.now()


@pytest.fixture
def closed_episode(issue, now):
    return models.Episode.objects.create(
        project=issue.project,
        issue=issue,
        am_fingerprint="0000aaaa1111bbbb",
        labels={
            "alertname": "TargetDown",
            "namespace": "monitoring",
            "job": "kube-state-metrics",
        },
        environment="p-mk1",
        starts_at=now - datetime.timedelta(hours=9),
        ends_at=now - datetime.timedelta(hours=8),
        delivery_count=3,
        last_delivery_at=now - datetime.timedelta(hours=8),
    )


@pytest.fixture
def open_episode(issue, now):
    return models.Episode.objects.create(
        project=issue.project,
        issue=issue,
        am_fingerprint="2222cccc3333dddd",
        labels={
            "alertname": "TargetDown",
            "namespace": "monitoring",
            "job": "node-exporter",
        },
        environment="p-mk1",
        starts_at=now - datetime.timedelta(hours=2),
        ends_at=None,
        delivery_count=2,
        last_delivery_at=now,
    )


@pytest.fixture
def tagged(issue):
    rows = [
        ("job", "node-exporter", 7),
        ("job", "kube-state-metrics", 3),
        ("severity", "warning", 10),
    ]
    for key, value, count in rows:
        models.TagStat.objects.create(issue=issue, key=key, value=value, count=count)
    return issue


@pytest.fixture
def audited(issue, now):
    models.IssueActivity.objects.create(
        issue=issue,
        kind=models.ActivityKind.CREATED,
        actor="",
        at=now - datetime.timedelta(hours=9),
        data={"annotations": {"summary": "1 of 4 targets is down", "runbook": "wiki"}},
    )
    models.IssueActivity.objects.create(
        issue=issue,
        kind=models.ActivityKind.ACKNOWLEDGED,
        actor="admin",
        at=now,
        data={"previous_triage_state": "new"},
    )
    return issue


# timeline shape


def test_the_timeline_names_the_columns_an_operator_reads(issue):
    """Should show when, how long, how often and what made this episode different."""
    result = [column.label for column in detail.build(issue).timeline.columns]
    expected = [
        "Started",
        "Ended",
        "Duration",
        "Deliveries",
        "Distinguishing labels",
    ]

    assert result == expected


def test_an_issue_without_episodes_says_so(issue):
    """Should render an empty timeline rather than an empty table."""
    timeline = detail.build(issue).timeline

    result = (timeline.rows, timeline.empty_message)
    expected = ((), "No episodes recorded")

    assert result == expected


def test_the_timeline_is_newest_first(issue, closed_episode, open_episode):
    """Should put what is happening now above what already ended."""
    rows = detail.build(issue).timeline.rows

    result = [row[0].text for row in rows]
    expected = [
        detail.components.format_stamp(open_episode.starts_at),
        detail.components.format_stamp(closed_episode.starts_at),
    ]

    assert result == expected


def test_the_timeline_stops_at_twenty_episodes(issue, now):
    """Should keep the detail page bounded on a long-running flapper."""
    for index in range(25):
        models.Episode.objects.create(
            project=issue.project,
            issue=issue,
            am_fingerprint=f"{index:016d}",
            labels={"alertname": "TargetDown"},
            starts_at=now - datetime.timedelta(hours=index + 1),
            ends_at=now - datetime.timedelta(hours=index),
            delivery_count=1,
            last_delivery_at=now - datetime.timedelta(hours=index),
        )

    result = len(detail.build(issue).timeline.rows)
    expected = 20

    assert result == expected


# timeline cells


def test_an_open_episode_is_flagged_rather_than_dated(issue, open_episode):
    """Should mark a live episode instead of printing a blank end stamp."""
    ended = detail.build(issue).timeline.rows[0][1]

    result = (ended.text, ended.variant)
    expected = ("firing", "danger")

    assert result == expected


def test_a_closed_episode_shows_the_length_it_ran(issue, closed_episode):
    """Should measure a finished episode between its own two stamps."""
    row = detail.build(issue).timeline.rows[0]

    result = row[2].text
    expected = "1h 0m"

    assert result == expected


def test_an_open_episode_is_measured_against_now(issue, open_episode):
    """Should keep counting while the alert is still firing."""
    row = detail.build(issue).timeline.rows[0]

    result = row[2].text
    expected = "2h 0m"

    assert result == expected


def test_the_delivery_count_is_shown_verbatim(issue, closed_episode):
    """Should surface repeat_interval re-sends as their own number."""
    result = detail.build(issue).timeline.rows[0][3].text
    expected = "3"

    assert result == expected


def test_only_the_labels_the_grouping_dropped_are_listed(issue, closed_episode):
    """Should answer 'which instance was this' without repeating the issue title."""
    result = detail.build(issue).timeline.rows[0][4].text
    expected = "job=kube-state-metrics"

    assert result == expected


def test_an_episode_matching_the_grouping_exactly_lists_nothing(issue, now):
    """Should stay quiet when the episode adds no labels of its own."""
    models.Episode.objects.create(
        project=issue.project,
        issue=issue,
        am_fingerprint="4444eeee5555ffff",
        labels=dict(issue.grouping_labels),
        starts_at=now - datetime.timedelta(hours=1),
        ends_at=now,
        delivery_count=1,
        last_delivery_at=now,
    )

    result = detail.build(issue).timeline.rows[0][4].text
    expected = ""

    assert result == expected


# tag distribution


def test_tags_are_grouped_by_key(tagged):
    """Should show one distribution per label key, not one flat list."""
    result = [group.key for group in detail.build(tagged).tags]
    expected = ["job", "severity"]

    assert result == expected


def test_a_group_totals_its_own_values(tagged):
    """Should let the reader see how many events the key was recorded on."""
    groups = {group.key: group for group in detail.build(tagged).tags}

    result = (groups["job"].total, groups["severity"].total)
    expected = (10, 10)

    assert result == expected


def test_bars_are_ordered_by_count_and_sized_against_the_group(tagged):
    """Should rank values and give each a share of its own key."""
    groups = {group.key: group for group in detail.build(tagged).tags}

    result = [(bar.label, bar.count, bar.percent) for bar in groups["job"].bars]
    expected = [("node-exporter", 7, 70), ("kube-state-metrics", 3, 30)]

    assert result == expected


def test_a_wide_key_is_capped_at_five_values(issue):
    """Should keep the sidebar readable when a key has a long tail."""
    for index in range(9):
        models.TagStat.objects.create(
            issue=issue,
            key="pod",
            value=f"ledger-{index}",
            count=index + 1,
        )

    result = len(detail.build(issue).tags[0].bars)
    expected = 5

    assert result == expected


def test_an_issue_without_tags_has_no_groups(issue):
    """Should return nothing to render rather than an empty placeholder group."""
    result = detail.build(issue).tags
    expected = ()

    assert result == expected


# enrichment links


def test_no_link_is_offered_when_no_template_is_configured(issue, settings):
    """Should stay quiet on a deployment that has no Grafana wired up."""
    settings.PANDORA_GRAFANA_URL = ""
    settings.PANDORA_LOKI_QUERY_URL = ""

    result = detail.build(issue).links
    expected = ()

    assert result == expected


def test_a_template_is_filled_from_the_grouping_labels(issue, settings):
    """Should let an operator template a deep link off the issue's own labels."""
    settings.PANDORA_GRAFANA_URL = "https://g.test/d?var-ns={namespace}"
    settings.PANDORA_LOKI_QUERY_URL = ""

    result = detail.build(issue).links[0]
    expected = detail.Link(label="Grafana", href="https://g.test/d?var-ns=monitoring")

    assert result == expected


def test_a_template_is_pinned_to_the_latest_episode_range(
    issue, settings, closed_episode, open_episode
):
    """Should point the reader at the window the newest episode covers."""
    settings.PANDORA_GRAFANA_URL = "https://g.test/explore?from={from_ms}&to={to_ms}"
    settings.PANDORA_LOKI_QUERY_URL = ""

    href = detail.build(issue).links[0].href

    result = href.split("from=")[1].split("&")[0]
    expected = str(int(open_episode.starts_at.timestamp() * 1000))

    assert result == expected


def test_an_issue_without_episodes_falls_back_to_its_own_window(issue, settings):
    """Should still produce a usable range for an SDK issue with no episodes."""
    settings.PANDORA_GRAFANA_URL = "https://g.test/explore?from={from_ms}&to={to_ms}"
    settings.PANDORA_LOKI_QUERY_URL = ""

    href = detail.build(issue).links[0].href

    result = href.split("from=")[1].split("&")[0]
    expected = str(int(issue.first_seen.timestamp() * 1000))

    assert result == expected


def test_a_template_asking_for_a_label_this_issue_lacks_is_dropped(issue, settings):
    """Should skip a link it cannot fill instead of rendering a broken URL."""
    settings.PANDORA_GRAFANA_URL = "https://g.test/d?pod={pod}"
    settings.PANDORA_LOKI_QUERY_URL = ""

    result = detail.build(issue).links
    expected = ()

    assert result == expected


def test_a_loki_template_can_escape_its_own_stream_selector(issue, settings):
    """Should let a LogQL selector survive the placeholder expansion."""
    settings.PANDORA_GRAFANA_URL = ""
    settings.PANDORA_LOKI_QUERY_URL = 'https://l.test/?q={{namespace="{namespace}"}}'

    result = detail.build(issue).links[0]
    expected = detail.Link(
        label="Loki",
        href='https://l.test/?q={namespace="monitoring"}',
    )

    assert result == expected


def test_the_project_slug_and_environment_are_available_to_templates(issue, settings):
    """Should expose the two fields that are never labels."""
    settings.PANDORA_GRAFANA_URL = "https://g.test/{project}/{environment}"
    settings.PANDORA_LOKI_QUERY_URL = ""

    result = detail.build(issue).links[0].href
    expected = "https://g.test/infrastructure/p-mk1"

    assert result == expected


# activity feed


def test_the_activity_feed_is_newest_first_with_readable_kinds(audited):
    """Should read as a story from the top down, not a raw enum dump."""
    result = [(row.kind, row.actor) for row in detail.build(audited).activities]
    expected = [("Acknowledged", "admin"), ("Created", "")]

    assert result == expected


def test_a_state_change_carries_the_state_it_left(audited):
    """Should let a reader see what an acknowledge actually replaced."""
    result = detail.build(audited).activities[0].note
    expected = "was new"

    assert result == expected


def test_an_activity_without_a_previous_state_carries_no_note(audited):
    """Should leave the note empty rather than inventing one."""
    result = detail.build(audited).activities[1].note
    expected = ""

    assert result == expected


def test_the_activity_feed_stops_at_twenty_rows(issue, now):
    """Should keep a noisy audit trail from swamping the page."""
    for index in range(25):
        models.IssueActivity.objects.create(
            issue=issue,
            kind=models.ActivityKind.REGRESSION,
            at=now - datetime.timedelta(minutes=index),
        )

    result = len(detail.build(issue).activities)
    expected = 20

    assert result == expected


# annotations


def test_annotations_come_from_the_activity_that_recorded_them(audited):
    """Should render what Alertmanager said, sorted for a stable page."""
    result = detail.build(audited).annotations
    expected = (("runbook", "wiki"), ("summary", "1 of 4 targets is down"))

    assert result == expected


def test_an_issue_without_annotations_renders_none(issue):
    """Should return nothing rather than an empty annotation row."""
    result = detail.build(issue).annotations
    expected = ()

    assert result == expected
