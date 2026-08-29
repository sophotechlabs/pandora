import datetime
import http

import pytest
from django.contrib.auth import models as auth_models
from django.utils import timezone

pytestmark = pytest.mark.django_db


def snooze(client, spec, issues, next_url="/"):
    return client.post(
        "/issues/actions/",
        {
            "issue": [str(issue.pk) for issue in issues],
            "action": f"snooze:{spec}",
            "next": next_url,
        },
    )


# from the action bar


def test_an_operator_can_snooze_from_the_stream(operator_client, make_issue):
    """Should be one click from the row that is being noisy."""
    issue = make_issue()

    snooze(operator_client, "1h", [issue])
    issue.refresh_from_db()

    result = issue.snoozed_until is not None

    assert result is True


def test_a_count_snooze_is_offered_too(operator_client, make_issue):
    """Should let an operator wait out a burst rather than a clock."""
    issue = make_issue()

    snooze(operator_client, "500", [issue])
    issue.refresh_from_db()

    result = issue.snoozed_past_count
    expected = issue.event_count + 500

    assert result == expected


def test_an_unknown_window_is_reported(operator_client, make_issue):
    """Should say so rather than appearing to work."""
    issue = make_issue()

    response = snooze(operator_client, "forever", [issue], next_url="/")
    issue.refresh_from_db()

    result = (response.status_code, issue.snoozed_until)
    expected = (http.HTTPStatus.FOUND, None)

    assert result == expected


def test_snoozing_needs_the_change_permission(client, make_issue):
    """Should be protected the way every other triage action is."""
    watcher = auth_models.User.objects.create_user(
        username="watcher",
        password="watcher-pass",
        is_staff=True,
    )
    client.force_login(watcher)
    issue = make_issue()

    response = snooze(client, "1h", [issue])
    issue.refresh_from_db()

    result = (response.status_code, issue.snoozed_until)
    expected = (http.HTTPStatus.FORBIDDEN, None)

    assert result == expected


def test_the_stream_offers_the_snooze_menu(operator_client, make_issue):
    """Should be findable without knowing the action name."""
    make_issue()

    page = operator_client.get("/").content.decode()

    result = ('value="snooze:1h"' in page, 'value="snooze:500"' in page)
    expected = (True, True)

    assert result == expected


def test_the_issue_page_offers_the_snooze_menu(operator_client, make_issue):
    """Should be available where a single issue is read, not only in bulk."""
    issue = make_issue()

    page = operator_client.get(f"/issues/{issue.pk}/").content.decode()

    result = 'value="snooze:1w"' in page

    assert result is True


# the query language


def test_snoozed_issues_can_be_listed(operator_client, make_issue):
    """Should let an operator see what they have quietened, or it is a black hole."""
    quiet = make_issue(title="Quiet one")
    quiet.snoozed_until = timezone.now() + datetime.timedelta(hours=1)
    quiet.save(update_fields=["snoozed_until"])
    make_issue(title="Loud one")

    page = operator_client.get("/", {"q": "is:snoozed"}).content.decode()

    result = ("Quiet one" in page, "Loud one" in page)
    expected = (True, False)

    assert result == expected


def test_awake_issues_exclude_the_snoozed_ones(operator_client, make_issue):
    """Should give the default queue a way to skip what is deliberately quiet."""
    quiet = make_issue(title="Quiet one")
    quiet.snoozed_until = timezone.now() + datetime.timedelta(hours=1)
    quiet.save(update_fields=["snoozed_until"])
    make_issue(title="Loud one")

    page = operator_client.get("/", {"q": "is:awake"}).content.decode()

    result = ("Quiet one" in page, "Loud one" in page)
    expected = (False, True)

    assert result == expected


def test_a_count_snooze_is_listed_as_snoozed(operator_client, make_issue):
    """Should treat both kinds of quiet the same way in the query language."""
    quiet = make_issue(title="Quiet one")
    quiet.snoozed_past_count = quiet.event_count + 100
    quiet.save(update_fields=["snoozed_past_count"])

    page = operator_client.get("/", {"q": "is:snoozed"}).content.decode()

    result = "Quiet one" in page

    assert result is True
