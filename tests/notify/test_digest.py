import datetime

import pytest
from django.core.management import call_command
from django.utils import timezone

from pandora.issues import models as issue_models
from pandora.notify import digest
from pandora.notify import models as notify_models

pytestmark = pytest.mark.django_db

NOW = timezone.now().replace(minute=30, second=0, microsecond=0)
HOUR = NOW.replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def make_issue(project):
    def build(digest_key="a", **overrides):
        fields = {
            "project": project,
            "fingerprint_hash": digest_key * 64,
            "title": f"issue {digest_key}",
            "first_seen": NOW - datetime.timedelta(days=1),
            "last_seen": NOW,
        }
        fields.update(overrides)
        return issue_models.Issue.objects.create(**fields)

    return build


def counted(issue, count, days_ago=0):
    issue_models.HourlyStat.objects.create(
        issue=issue,
        hour=HOUR - datetime.timedelta(days=days_ago),
        count=count,
    )


def test_the_summary_counts_the_events_in_the_period(project, make_issue):
    """Should be the number the report leads with."""
    counted(make_issue(), 40)

    summary = digest.build(project, NOW)

    assert summary.events == 40


def test_events_from_before_the_period_are_not_counted(project, make_issue):
    """Should be a report about the week, not about everything."""
    issue = make_issue()
    counted(issue, 40)
    counted(issue, 900, days_ago=20)

    summary = digest.build(project, NOW)

    assert summary.events == 40


def test_the_summary_counts_the_new_issues(project, make_issue):
    """Should say what appeared, which is what a reader scans for."""
    make_issue("a")
    make_issue("b", first_seen=NOW - datetime.timedelta(days=30))

    summary = digest.build(project, NOW)

    assert summary.new_issues == 1


def test_the_summary_counts_what_was_resolved(project, make_issue):
    """Should credit the week's triage, not only its faults."""
    issue = make_issue()
    issue_models.IssueActivity.objects.create(
        issue=issue, kind=issue_models.ActivityKind.RESOLVED, at=NOW
    )

    summary = digest.build(project, NOW)

    assert summary.resolved_issues == 1


def test_the_summary_counts_regressions(project, make_issue):
    """Should separate a fault coming back from a fault appearing."""
    issue = make_issue()
    issue_models.IssueActivity.objects.create(
        issue=issue, kind=issue_models.ActivityKind.REGRESSION, at=NOW
    )

    summary = digest.build(project, NOW)

    assert summary.regressed_issues == 1


def test_the_summary_compares_against_the_period_before(project, make_issue):
    """Should answer 'better or worse than last week' without a chart."""
    issue = make_issue()
    counted(issue, 40)
    counted(issue, 20, days_ago=8)

    summary = digest.build(project, NOW)

    assert summary.change == 100.0


def test_a_period_with_no_history_has_no_comparison(project, make_issue):
    """Should not print an infinite rise for a first week."""
    counted(make_issue(), 40)

    assert digest.build(project, NOW).change is None


def test_the_summary_names_the_loudest_issues(project, make_issue):
    """Should give a reader the three things worth opening."""
    counted(make_issue("a", title="Loud"), 500)
    counted(make_issue("b", title="Quiet"), 2)

    summary = digest.build(project, NOW)

    assert [row.title for row in summary.top] == ["Loud", "Quiet"]


def test_the_top_list_is_bounded(project, make_issue):
    """Should not paste the whole backlog into an email."""
    for index in range(10):
        counted(make_issue(f"{index}", fingerprint_hash=f"{index:064d}"), 10 + index)

    summary = digest.build(project, NOW)

    assert len(summary.top) == digest.TOP_ISSUES


def test_the_period_can_be_a_day(project, make_issue):
    """Should support a daily report for a noisy install."""
    issue = make_issue()
    counted(issue, 40)
    counted(issue, 900, days_ago=3)

    summary = digest.build(project, NOW, period="day")

    assert summary.events == 40


def test_an_unknown_period_falls_back_to_a_week():
    """Should not raise on a value the command already validated."""
    assert digest.window_for("fortnight") == digest.DEFAULT_PERIOD


def test_the_summary_reads_as_lines(project, make_issue):
    """Should be what the email body and the cron log both print."""
    counted(make_issue(title="Loud"), 40)

    lines = digest.build(project, NOW).lines()

    assert lines[0] == "infrastructure: 40 events over the last week"


def test_the_report_is_queued_to_a_subscribed_destination(project, make_issue):
    """Should reach the operator without a second delivery mechanism."""
    counted(make_issue(), 40)
    notify_models.Destination.objects.create(
        name="ops",
        kind=notify_models.DestinationKind.WEBHOOK,
        target="https://hooks.test/ops",
        events=[notify_models.REPORT],
    )

    digest.send(NOW)

    delivery = notify_models.Delivery.objects.get()
    result = (delivery.event, delivery.payload["events"])
    expected = (notify_models.REPORT, 40)

    assert result == expected


def test_a_destination_that_did_not_ask_gets_nothing(project, make_issue):
    """Should not mail a weekly report to an incident webhook."""
    counted(make_issue(), 40)
    notify_models.Destination.objects.create(
        name="ops",
        kind=notify_models.DestinationKind.WEBHOOK,
        target="https://hooks.test/ops",
        events=[notify_models.NEW],
    )

    digest.send(NOW)

    assert notify_models.Delivery.objects.count() == 0


def test_a_project_with_no_issues_queues_nothing(project):
    """Should not send an empty report for a project nobody uses."""
    notify_models.Destination.objects.create(
        name="ops",
        kind=notify_models.DestinationKind.WEBHOOK,
        target="https://hooks.test/ops",
        events=[notify_models.REPORT],
    )

    digest.send(NOW)

    assert notify_models.Delivery.objects.count() == 0


def test_the_command_prints_the_summary(project, make_issue, capsys):
    """Should be readable from a cron log without opening the UI."""
    counted(make_issue(), 40)

    call_command("report", "--period", "week")

    assert "40 events over the last week" in capsys.readouterr().out


def test_a_dry_run_queues_nothing(project, make_issue):
    """Should let an operator see the report before wiring a destination."""
    counted(make_issue(), 40)
    notify_models.Destination.objects.create(
        name="ops",
        kind=notify_models.DestinationKind.WEBHOOK,
        target="https://hooks.test/ops",
        events=[notify_models.REPORT],
    )

    call_command("report", "--dry-run")

    assert notify_models.Delivery.objects.count() == 0


def test_the_email_body_reads_the_report(project, make_issue):
    """Should not print an empty issue line for a delivery with no issue."""
    from pandora.notify import senders

    counted(make_issue(), 40)
    notify_models.Destination.objects.create(
        name="ops",
        kind=notify_models.DestinationKind.EMAIL,
        target="ops@example.test",
        events=[notify_models.REPORT],
    )
    digest.send(NOW)

    lines = senders._lines(list(notify_models.Delivery.objects.all()))

    assert "40 events" in lines[0]


def test_an_email_destination_reaches_the_subscribers_too(project, make_issue):
    """Should let a comment reach the people watching, not only the team list."""
    from django.core import mail

    from pandora.notify import senders

    destination = notify_models.Destination.objects.create(
        name="ops",
        kind=notify_models.DestinationKind.EMAIL,
        target="ops@example.test",
        events=[notify_models.COMMENT],
    )
    issue = make_issue()
    delivery = notify_models.Delivery.objects.create(
        destination=destination,
        issue=issue,
        event=notify_models.COMMENT,
        payload={"issue": {"title": "boom"}, "subscribers": ["dev@example.test"]},
    )

    senders.send_email(destination, [delivery])

    assert sorted(mail.outbox[0].to) == ["dev@example.test", "ops@example.test"]


def test_a_destination_with_no_recipients_at_all_is_reported(project, make_issue):
    """Should say why nothing was sent rather than fail silently."""
    from pandora.notify import senders

    destination = notify_models.Destination.objects.create(
        name="ops",
        kind=notify_models.DestinationKind.EMAIL,
        target="",
        events=[notify_models.COMMENT],
    )
    issue = make_issue()
    delivery = notify_models.Delivery.objects.create(
        destination=destination,
        issue=issue,
        event=notify_models.COMMENT,
        payload={"issue": {"title": "boom"}},
    )

    with pytest.raises(senders.SendError):
        senders.send_email(destination, [delivery])
