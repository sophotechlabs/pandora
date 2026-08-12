import datetime

import freezegun
import pytest
from django.utils import timezone

from pandora.am import client as am_client
from pandora.issues import actions, models

pytestmark = pytest.mark.django_db

FROZEN = "2026-08-04 12:00:00"


@pytest.fixture(autouse=True)
def frozen():
    with freezegun.freeze_time(FROZEN):
        yield


class RefusingClient:
    def create_silence(self, **kwargs):
        raise am_client.AlertmanagerError("alertmanager said no")


class RecordingClient:
    def __init__(self):
        self.created = []

    def create_silence(self, **kwargs):
        self.created.append(kwargs)
        return f"silence-{len(self.created)}"


# the windows the two surfaces share


def test_the_three_silence_windows_are_declared_once():
    """Should keep the admin and the UI offering the same durations."""
    result = dict(actions.SILENCE_WINDOWS)
    expected = {
        "1h": datetime.timedelta(hours=1),
        "4h": datetime.timedelta(hours=4),
        "1d": datetime.timedelta(days=1),
    }

    assert result == expected


def test_every_target_state_has_a_verb():
    """Should have a word to report back for each transition a human can make."""
    result = sorted(actions.TRIAGE_VERBS)
    expected = sorted(models.TriageState.values[1:])

    assert result == expected


# applying triage


def test_applying_a_transition_moves_the_issue(issue):
    """Should write the new state and say that it changed."""
    changed = actions.apply_triage(issue, "resolved", "operator", timezone.now())

    issue.refresh_from_db()

    result = (changed, issue.triage_state, issue.last_resolved_at)
    expected = (True, models.TriageState.RESOLVED, timezone.now())

    assert result == expected


def test_applying_the_state_it_already_holds_changes_nothing(issue):
    """Should keep a second click out of the audit trail."""
    issue.triage_state = models.TriageState.ACKNOWLEDGED
    issue.save(update_fields=["triage_state"])

    changed = actions.apply_triage(issue, "ack", "operator", timezone.now())

    result = (changed, models.IssueActivity.objects.count())
    expected = (False, 0)

    assert result == expected


def test_a_batch_reports_what_moved_and_what_did_not(issue, project):
    """Should let both surfaces phrase the same sentence."""
    already = models.Issue.objects.create(
        project=project,
        fingerprint_hash="b" * 64,
        title="Already",
        triage_state=models.TriageState.RESOLVED,
    )

    report = actions.retriage([issue, already], "resolved", "operator", timezone.now())

    result = (report.changed, report.unchanged)
    expected = (1, 1)

    assert result == expected


# silences


def test_a_batch_silence_counts_what_it_sent(issue):
    """Should report one silence per issue that Alertmanager accepted."""
    report = actions.silence(
        [issue],
        datetime.timedelta(hours=1),
        "operator",
        RecordingClient(),
    )

    result = (report.silenced, report.errors)
    expected = (1, ())

    assert result == expected


def test_a_refused_silence_is_carried_back_as_a_note(issue):
    """Should name the issue that failed rather than dropping the error."""
    report = actions.silence(
        [issue],
        datetime.timedelta(hours=1),
        "operator",
        RefusingClient(),
    )

    result = (report.silenced, len(report.errors))
    expected = (0, 1)

    assert result == expected
    assert "alertmanager said no" in report.errors[0]


def test_one_refusal_does_not_stop_the_rest(issue, project):
    """Should keep silencing the selection after one issue is refused."""
    unlabelled = models.Issue.objects.create(
        project=project,
        fingerprint_hash="c" * 64,
        title="No labels",
        grouping_labels={},
    )

    report = actions.silence(
        [unlabelled, issue],
        datetime.timedelta(hours=1),
        "operator",
        RecordingClient(),
    )

    result = (report.silenced, len(report.errors))
    expected = (1, 1)

    assert result == expected
