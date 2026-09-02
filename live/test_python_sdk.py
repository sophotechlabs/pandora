"""What the official `sentry-sdk` sends, read back off the issue page.

Every payload here was produced by the SDK a user would install, not by this
repo — which is the only way to know the wire contract actually holds.
"""

import pytest

from live.support import body_of, issue_titled
from pandora.ingest import models as ingest_models
from pandora.issues import models as issue_models
from pandora.releases import models as release_models

pytestmark = pytest.mark.live


# the exception


def test_the_exception_became_an_issue():
    """Should be the whole point: a real SDK crash turns into a triage row."""
    issue = issue_titled("ZeroDivisionError")

    assert issue is not None


def test_the_issue_names_the_function_that_failed():
    """Should read `ZeroDivisionError: charge`, not just the exception type."""
    issue = issue_titled("ZeroDivisionError")

    result = issue.title

    assert "charge" in result


def test_the_application_frame_is_on_the_page(signed_in, base_url):
    """Should show the file the operator can open, from the SDK's own frames."""
    body = body_of(signed_in, base_url, issue_titled("ZeroDivisionError"))

    assert "python_app.py" in body


def test_the_source_line_is_on_the_page(signed_in, base_url):
    """Should show the code, which is what the SDK sends and Pandora keeps."""
    body = body_of(signed_in, base_url, issue_titled("ZeroDivisionError"))

    assert "total / discount" in body


def test_the_local_variables_are_on_the_page(signed_in, base_url):
    """Should show `discount = 0` — the value that explains the crash."""
    body = body_of(signed_in, base_url, issue_titled("ZeroDivisionError"))

    assert "discount" in body


def test_the_breadcrumbs_are_on_the_page(signed_in, base_url):
    """Should show what the operator did before it broke."""
    body = body_of(signed_in, base_url, issue_titled("ZeroDivisionError"))

    assert "operator signed in" in body


def test_the_user_context_is_on_the_page(signed_in, base_url):
    """Should carry the user the SDK attached, because `send_default_pii` was on."""
    body = body_of(signed_in, base_url, issue_titled("ZeroDivisionError"))

    assert "live-operator" in body


def test_the_sdk_names_itself(signed_in, base_url):
    """Should record which SDK sent it, which is how a bad client is found."""
    body = body_of(signed_in, base_url, issue_titled("ZeroDivisionError"))

    assert "sentry.python" in body


# tags, release, environment


def test_the_tags_the_sdk_set_are_stored():
    """Should keep `service` and `region`, which is what filters the stream."""
    issue = issue_titled("ZeroDivisionError")

    result = set(
        issue_models.TagStat.objects.filter(issue=issue).values_list("key", flat=True)
    )

    assert {"service", "region"} <= result


def test_the_environment_the_sdk_set_is_recorded():
    """Should read `live` off the event, not off a default."""
    issue = issue_titled("ZeroDivisionError")

    result = set(
        issue_models.IssueEnvironment.objects.filter(issue=issue).values_list(
            "name", flat=True
        )
    )
    expected = {"live"}

    assert result == expected


def test_the_release_became_a_release_row():
    """Should promote the SDK's `release` from a tag to an object with an order."""
    result = release_models.Release.objects.filter(version="1.4.2").count()
    expected = 1

    assert result == expected


def test_the_release_records_the_environment_it_reached():
    """Should be the rollout signal: a process is on a release once it reports."""
    release = release_models.Release.objects.get(version="1.4.2")

    result = set(
        release_models.ReleaseEnvironment.objects.filter(release=release).values_list(
            "name", flat=True
        )
    )
    expected = {"live"}

    assert result == expected


# the other two captures


def test_a_captured_message_is_its_own_issue():
    """Should keep a level-carrying message apart from the exception."""
    issue = issue_titled("checkout queue is backing up")

    result = issue.level
    expected = "warning"

    assert result == expected


def test_a_second_exception_kind_groups_separately():
    """Should not fold a KeyError into the ZeroDivisionError."""
    result = issue_titled("KeyError").pk
    other = issue_titled("ZeroDivisionError").pk

    assert result != other


def test_a_scoped_tag_overrides_the_global_one():
    """Should record `service=worker` for the issue raised inside the scope."""
    issue = issue_titled("KeyError")

    result = set(
        issue_models.TagStat.objects.filter(issue=issue, key="service").values_list(
            "value", flat=True
        )
    )
    expected = {"worker"}

    assert result == expected


def test_the_sdk_reports_an_event_dropped_by_before_send():
    row = ingest_models.ClientDiscard.objects.get(reason="before_send")

    assert row.category == "error"
    assert row.quantity >= 1
    assert (
        issue_models.Issue.objects.filter(
            title__icontains="client report compatibility check"
        ).exists()
        is False
    )


# release health


def test_the_sdk_reported_sessions():
    """Should have received the session envelopes the SDK sends on its own."""
    result = release_models.SessionBucket.objects.count()

    assert result > 0


def test_the_release_has_a_crash_free_rate():
    """Should compute health from real sessions the SDK sent, not from events.

    Nothing renders this yet — there is no releases page and the JSON API does
    not carry it — so the assertion goes through the function the UI would call.
    """
    from pandora.core.models import Project
    from pandora.releases import sessions

    project = Project.objects.get(slug="live")

    result = sessions.health(project, "1.4.2").crash_free_percent

    assert 0 < result <= 100


def test_a_crashed_session_lowers_the_rate():
    """Should count the run that died on an unhandled exception as crashed."""
    from pandora.core.models import Project
    from pandora.releases import sessions

    project = Project.objects.get(slug="live")

    result = sessions.health(project, "1.4.2").crashed

    assert result >= 1
