import datetime

import pytest
from django.core.management import call_command
from django.utils import timezone

from pandora.issues import models as issue_models
from pandora.notify import metrics
from pandora.notify import models as notify_models
from pandora.releases import models as release_models

pytestmark = pytest.mark.django_db

NOW = timezone.now().replace(minute=30, second=0, microsecond=0)
HOUR = NOW.replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def monitor(project):
    def build(**overrides):
        fields = {
            "name": "error rate",
            "project": project,
            "dataset": notify_models.MetricDataset.EVENTS,
            "window_minutes": 60,
            "comparison": notify_models.Comparison.ABOVE,
            "threshold": 100,
        }
        fields.update(overrides)
        return notify_models.MetricMonitor.objects.create(**fields)

    return build


@pytest.fixture
def make_issue(project):
    def build(digest="a", **overrides):
        fields = {
            "project": project,
            "fingerprint_hash": digest * 64,
            "title": f"issue {digest}",
            "level": issue_models.Level.ERROR,
            "first_seen": NOW - datetime.timedelta(minutes=10),
            "last_seen": NOW,
        }
        fields.update(overrides)
        return issue_models.Issue.objects.create(**fields)

    return build


def counted(issue, count, hours_ago=0):
    issue_models.HourlyStat.objects.create(
        issue=issue,
        hour=HOUR - datetime.timedelta(hours=hours_ago),
        count=count,
    )


# measuring


def test_the_event_count_is_summed_over_the_window(monitor, make_issue):
    """Should read the counters the stream already keeps, not a new table."""
    counted(make_issue(), 40)

    assert metrics.value_of(monitor(), NOW) == 40


def test_events_outside_the_window_are_not_counted(monitor, make_issue):
    """Should measure a window, which is what makes a rate a rate."""
    issue = make_issue()
    counted(issue, 40)
    counted(issue, 900, hours_ago=6)

    assert metrics.value_of(monitor(), NOW) == 40


def test_the_issue_count_is_distinct(monitor, make_issue):
    """Should answer how many things are broken, not how loudly."""
    counted(make_issue("a"), 500)
    counted(make_issue("b"), 1)

    found = monitor(dataset=notify_models.MetricDataset.ISSUES)

    assert metrics.value_of(found, NOW) == 2


def test_new_issues_count_only_what_first_appeared_in_the_window(monitor, make_issue):
    """Should be the number a release regression actually moves."""
    make_issue("a", first_seen=NOW - datetime.timedelta(minutes=5))
    make_issue("b", first_seen=NOW - datetime.timedelta(days=4))

    found = monitor(dataset=notify_models.MetricDataset.NEW_ISSUES)

    assert metrics.value_of(found, NOW) == 1


def test_the_user_count_reads_first_sightings(monitor, make_issue):
    """Should count people newly reached, not people counted again."""
    issue = make_issue()
    issue_models.IssueUser.objects.create(
        issue=issue, key="id:1", first_seen=NOW - datetime.timedelta(minutes=5)
    )
    issue_models.IssueUser.objects.create(
        issue=issue, key="id:2", first_seen=NOW - datetime.timedelta(days=3)
    )

    found = monitor(dataset=notify_models.MetricDataset.USERS)

    assert metrics.value_of(found, NOW) == 1


def test_the_crash_free_rate_is_a_percentage(monitor, project):
    """Should be the release-health number people actually put an alert on."""
    release_models.SessionBucket.objects.create(
        project=project, hour=HOUR, sessions=100, crashed=5
    )

    found = monitor(dataset=notify_models.MetricDataset.CRASH_FREE)

    assert metrics.value_of(found, NOW) == 95.0


def test_no_sessions_means_no_measurement(monitor):
    """Should not report a hundred percent crash-free for an idle service."""
    found = monitor(dataset=notify_models.MetricDataset.CRASH_FREE)

    assert metrics.value_of(found, NOW) is None


def test_a_query_scopes_the_measurement(monitor, make_issue):
    """Should reuse the stream's own filter language rather than invent one."""
    counted(make_issue("a", level=issue_models.Level.ERROR), 40)
    counted(make_issue("b", level=issue_models.Level.INFO), 900)

    found = monitor(query="level:error")

    assert metrics.value_of(found, NOW) == 40


def test_an_environment_scopes_the_measurement(monitor, make_issue, project):
    """Should let staging noise stay out of a production threshold."""
    from pandora.issues import environments

    wanted = make_issue("a")
    environments.record(wanted, "production", NOW)
    counted(wanted, 40)
    other = make_issue("b")
    environments.record(other, "staging", NOW)
    counted(other, 900)

    found = monitor(environment="production")

    assert metrics.value_of(found, NOW) == 40


def test_a_comparison_delta_measures_the_change(monitor, make_issue):
    """Should answer 'is this worse than yesterday', which no fixed number can."""
    issue = make_issue()
    counted(issue, 40)
    counted(issue, 20, hours_ago=24)

    found = monitor(comparison_delta_minutes=24 * 60, threshold=50)

    assert metrics.value_of(found, NOW) == 100.0


def test_a_comparison_against_nothing_is_no_measurement(monitor, make_issue):
    """Should not report an infinite rise against a window that was empty."""
    counted(make_issue(), 40)

    found = monitor(comparison_delta_minutes=24 * 60)

    assert metrics.value_of(found, NOW) is None


# thresholds


def test_a_value_over_the_threshold_breaches(monitor):
    """Should be the plain reading of 'above'."""
    assert metrics.breached(monitor(threshold=100), 101) is True


def test_a_value_at_the_threshold_does_not_breach(monitor):
    """Should not fire on the number the operator called acceptable."""
    assert metrics.breached(monitor(threshold=100), 100) is False


def test_a_below_comparison_inverts_the_test(monitor):
    """Should support crash-free rates, which fail by falling."""
    found = monitor(comparison=notify_models.Comparison.BELOW, threshold=99)

    assert metrics.breached(found, 95) is True


# the sweep


def test_a_breach_opens_an_alert(monitor, make_issue):
    """Should end as an issue in the stream, like every other alert."""
    counted(make_issue(), 500)
    found = monitor()

    report = metrics.sweep(NOW)

    found.refresh_from_db()
    issue = issue_models.Issue.objects.get(title__contains="error rate")
    result = (report.fired, found.state, issue.source_state)
    expected = (
        [found.pk],
        notify_models.MonitorState.FIRING,
        issue_models.SourceState.FIRING,
    )

    assert result == expected


def test_a_second_sweep_while_still_breaching_opens_nothing_new(monitor, make_issue):
    """Should not re-page every five minutes for the same breach."""
    counted(make_issue(), 500)
    monitor()

    metrics.sweep(NOW)
    report = metrics.sweep(NOW + datetime.timedelta(minutes=5))

    assert report.fired == []
    assert issue_models.Episode.objects.count() == 1


def test_recovering_closes_the_alert(monitor, make_issue):
    """Should close the same episode it opened, not open a second one."""
    issue = make_issue()
    counted(issue, 500)
    found = monitor()
    metrics.sweep(NOW)

    issue_models.HourlyStat.objects.filter(issue=issue).update(count=1)
    report = metrics.sweep(NOW + datetime.timedelta(minutes=5))

    found.refresh_from_db()
    episode = issue_models.Episode.objects.get()
    result = (report.resolved, found.state, episode.ends_at is not None)
    expected = ([found.pk], notify_models.MonitorState.OK, True)

    assert result == expected


def test_a_quiet_monitor_never_fires(monitor, make_issue):
    """Should stay silent when nothing is wrong."""
    counted(make_issue(), 3)
    monitor()

    report = metrics.sweep(NOW)

    assert report.fired == []
    assert issue_models.Issue.objects.filter(title__contains="error rate").count() == 0


def test_an_inactive_monitor_is_not_evaluated(monitor, make_issue):
    """Should be a switch an operator can turn off without deleting."""
    counted(make_issue(), 500)
    monitor(active=False)

    report = metrics.sweep(NOW)

    assert report.evaluated == 0


def test_every_evaluation_is_recorded(monitor, make_issue):
    """Should let a reader see the run history Sentry shows for an alert rule."""
    counted(make_issue(), 500)
    found = monitor()

    metrics.sweep(NOW)

    run = notify_models.MetricRun.objects.get(monitor=found)
    result = (run.value, run.state)
    expected = (500.0, notify_models.MonitorState.FIRING)

    assert result == expected


def test_a_monitor_with_nothing_to_measure_records_no_data(monitor):
    """Should distinguish 'nothing happened' from 'everything is fine'."""
    found = monitor(dataset=notify_models.MetricDataset.CRASH_FREE)

    metrics.sweep(NOW)

    found.refresh_from_db()
    assert found.state == notify_models.MonitorState.NO_DATA


def test_the_run_history_is_bounded(monitor, make_issue):
    """Should not grow a row per evaluation forever on a five-minute cron."""
    found = monitor()
    notify_models.MetricRun.objects.bulk_create(
        notify_models.MetricRun(
            monitor=found,
            at=NOW - datetime.timedelta(minutes=index),
            value=1,
            state=notify_models.MonitorState.OK,
        )
        for index in range(1, metrics.RUN_HISTORY + 50)
    )

    metrics.sweep(NOW)

    assert notify_models.MetricRun.objects.count() == metrics.RUN_HISTORY


def test_the_alert_carries_the_reading_that_opened_it(monitor, make_issue):
    """Should tell a reader the number, not only that a rule matched."""
    counted(make_issue(), 500)
    monitor()

    metrics.sweep(NOW)

    issue = issue_models.Issue.objects.get(title__contains="error rate")
    assert "500" in issue.title or "500" in (issue.culprit or "")


def test_the_alert_is_labelled_as_a_monitor(monitor, make_issue):
    """Should be distinguishable from an alert Alertmanager actually sent."""
    counted(make_issue(), 500)
    found = monitor()

    metrics.sweep(NOW)

    payload = metrics.payload_for(found, 500, NOW, firing=True)
    assert payload["commonLabels"]["source"] == "pandora-metric-monitor"


def test_the_command_reports_what_it_did(monitor, make_issue, capsys):
    """Should print one line a cron log can be read from."""
    counted(make_issue(), 500)
    monitor()

    call_command("alerts")

    assert "alerts: fired 1" in capsys.readouterr().out


# performance thresholds


@pytest.fixture
def endpoint(project):
    from pandora.perf import service as perf

    def build(name="GET /checkout", duration_ms=120, failed=False, count=1):
        started = NOW.timestamp()
        status = "ok"
        if failed:
            status = "internal_error"
        for _ in range(count):
            perf.record(
                project,
                {
                    "type": "transaction",
                    "transaction": name,
                    "start_timestamp": started,
                    "timestamp": started + duration_ms / 1000,
                    "contexts": {"trace": {"status": status}},
                },
                NOW,
            )

    return build


def test_throughput_reads_the_transaction_buckets(monitor, endpoint):
    """Should let a threshold sit on traffic without a span store."""
    endpoint(count=3)

    found = monitor(dataset=notify_models.MetricDataset.THROUGHPUT)

    assert metrics.value_of(found, NOW) == 3


def test_the_ninety_fifth_percentile_is_measurable(monitor, endpoint):
    """Should be the latency number people actually alert on."""
    endpoint(duration_ms=800, count=10)

    found = monitor(dataset=notify_models.MetricDataset.LATENCY_P95)

    assert metrics.value_of(found, NOW) == 1000.0


def test_the_failure_rate_is_measurable(monitor, endpoint):
    """Should separate a slow endpoint from a broken one."""
    endpoint(count=3)
    endpoint(failed=True, count=1)

    found = monitor(dataset=notify_models.MetricDataset.FAILURE_RATE)

    assert metrics.value_of(found, NOW) == 25.0


def test_a_transaction_name_scopes_a_performance_monitor(monitor, endpoint):
    """Should let one monitor watch one family of endpoints."""
    endpoint(name="GET /api/orders", count=5)
    endpoint(name="GET /health", count=50)

    found = monitor(dataset=notify_models.MetricDataset.THROUGHPUT, query="GET /api/*")

    assert metrics.value_of(found, NOW) == 5


def test_a_performance_monitor_with_no_traffic_has_no_value(monitor):
    """Should say 'no data' rather than fire because nothing was called."""
    found = monitor(dataset=notify_models.MetricDataset.LATENCY_P95)

    assert metrics.value_of(found, NOW) is None


def test_a_latency_breach_opens_an_alert(monitor, endpoint):
    """Should end in the same stream as every other thing that is wrong."""
    endpoint(duration_ms=3000, count=5)
    found = monitor(
        name="checkout latency",
        dataset=notify_models.MetricDataset.LATENCY_P95,
        threshold=1000,
    )

    report = metrics.sweep(NOW)

    assert report.fired == [found.pk]
    assert issue_models.Issue.objects.filter(
        title__contains="checkout latency"
    ).exists()
