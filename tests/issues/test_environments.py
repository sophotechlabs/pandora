import datetime
import io

import pytest
from django.core import management
from django.utils import timezone

from pandora.issues import environments
from pandora.issues import models as issue_models

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def names():
    return sorted(issue_models.IssueEnvironment.objects.values_list("name", flat=True))


# recording where an issue was seen


def test_the_first_occurrence_creates_the_row(issue):
    """Should start counting the moment an issue is seen somewhere."""
    issue_models.IssueEnvironment.objects.all().delete()

    environments.record(issue, "p-mk2", NOW)

    row = issue_models.IssueEnvironment.objects.get()
    result = (row.name, row.event_count, row.first_seen, row.last_seen)
    expected = ("p-mk2", 1, NOW, NOW)

    assert result == expected


def test_a_second_occurrence_moves_last_seen_and_the_count(issue):
    """Should not mint a row per event — one row per place it fires."""
    issue_models.IssueEnvironment.objects.all().delete()
    later = NOW + datetime.timedelta(hours=2)
    environments.record(issue, "p-mk2", NOW)

    environments.record(issue, "p-mk2", later)

    row = issue_models.IssueEnvironment.objects.get()
    result = (row.event_count, row.first_seen, row.last_seen)
    expected = (2, NOW, later)

    assert result == expected


def test_out_of_order_occurrences_keep_the_full_environment_window(issue):
    issue_models.IssueEnvironment.objects.all().delete()
    earlier = NOW - datetime.timedelta(hours=2)
    environments.record(issue, "p-mk2", NOW)

    environments.record(issue, "p-mk2", earlier)

    row = issue_models.IssueEnvironment.objects.get()
    assert row.event_count == 2
    assert row.first_seen == earlier
    assert row.last_seen == NOW


def test_a_second_environment_gets_its_own_row(issue):
    """Should be the whole reason the table exists — one issue, several clusters."""
    issue_models.IssueEnvironment.objects.all().delete()
    environments.record(issue, "p-mk1", NOW)

    environments.record(issue, "p-mk2", NOW)

    result = names()
    expected = ["p-mk1", "p-mk2"]

    assert result == expected


def test_an_issue_with_no_environment_still_gets_a_row(issue):
    """Should keep the shape uniform for an SDK that never set one."""
    issue_models.IssueEnvironment.objects.all().delete()

    environments.record(issue, "", NOW)

    result = issue_models.IssueEnvironment.objects.get().name
    expected = ""

    assert result == expected


def test_the_names_of_an_issue_come_back_sorted(issue):
    """Should read the same way twice, which a set would not."""
    issue_models.IssueEnvironment.objects.all().delete()
    environments.record(issue, "p-mk2", NOW)
    environments.record(issue, "p-mk1", NOW)

    result = environments.names_of(issue)
    expected = ["p-mk1", "p-mk2"]

    assert result == expected


def test_a_row_reads_as_the_issue_and_the_place(issue):
    """Should be legible in the admin without opening it."""
    issue_models.IssueEnvironment.objects.all().delete()
    environments.record(issue, "p-mk2", NOW)

    result = str(issue_models.IssueEnvironment.objects.get())
    expected = f"{issue.pk} in p-mk2"

    assert result == expected


def test_a_row_with_no_name_says_so(issue):
    """Should not render an empty cell where a place would go."""
    issue_models.IssueEnvironment.objects.all().delete()
    environments.record(issue, "", NOW)

    result = str(issue_models.IssueEnvironment.objects.get())
    expected = f"{issue.pk} in no environment"

    assert result == expected


def test_deleting_the_issue_takes_its_environments(issue):
    """Should not leave rows pointing at an issue prune has taken."""
    issue.delete()

    result = issue_models.IssueEnvironment.objects.count()
    expected = 0

    assert result == expected


# the command


def test_the_command_reports_nothing_to_do(issue):
    """Should be safe to run on an install that was never split."""
    out = io.StringIO()

    management.call_command("merge_issues", "--dry-run", stdout=out)

    assert "0 fingerprint(s) with duplicates" in out.getvalue()


def test_a_dry_run_says_it_wrote_nothing(issue):
    """Should let an operator look before the migration does it for them."""
    out = io.StringIO()

    management.call_command("merge_issues", "--dry-run", stdout=out)

    assert "nothing written" in out.getvalue()


def test_a_real_run_is_recorded_in_the_history(issue):
    """Should show up on /history/ like every other thing that changed data."""
    from pandora.people.models import AuditEntry

    management.call_command("merge_issues", stdout=io.StringIO())

    result = AuditEntry.objects.get().target
    expected = "merge_issues"

    assert result == expected


def test_a_dry_run_writes_no_history(issue):
    """Should not log a change it did not make."""
    from pandora.people.models import AuditEntry

    management.call_command("merge_issues", "--dry-run", stdout=io.StringIO())

    assert AuditEntry.objects.count() == 0
