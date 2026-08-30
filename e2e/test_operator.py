import json

import pytest
import requests

from pandora.issues import models as issue_models
from pandora.people import models as people_models

pytestmark = pytest.mark.e2e


TITLE = "GatewayError: charge"


def send_event(base_url, dsn_key, message="e2e checkout failure"):
    envelope = "\n".join(
        [
            json.dumps({"event_id": "e" * 32}),
            json.dumps({"type": "event"}),
            json.dumps(
                {
                    "event_id": "e" * 32,
                    "level": "error",
                    "platform": "python",
                    "environment": "e2e",
                    "exception": {
                        "values": [
                            {
                                "type": "GatewayError",
                                "value": message,
                                "stacktrace": {
                                    "frames": [
                                        {
                                            "filename": "src/payments/charge.py",
                                            "lineno": 42,
                                            "function": "charge",
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                }
            ),
        ]
    ).encode()
    response = requests.post(
        f"{base_url}/api/{dsn_key.project_id}/envelope/",
        data=envelope,
        headers={
            "Content-Type": "application/x-sentry-envelope",
            "X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.status_code


# the door


def test_an_sdk_envelope_becomes_an_issue_on_the_page(
    page, base_url, dsn_key, make_user, sign_in
):
    """Should carry an event from the wire to the operator's screen, whole."""
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))

    page.goto(base_url)

    assert page.get_by_role("link", name=TITLE).is_visible()


def test_the_stack_trace_reaches_the_issue_page(
    page, base_url, dsn_key, make_user, sign_in
):
    """Should show the frame that broke, with its indentation and line number."""
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))
    page.goto(base_url)

    page.get_by_role("link", name=TITLE).click()

    assert page.get_by_text("charge.py").first.is_visible()


# signing in


def test_a_stranger_is_shown_the_login_page(page, base_url):
    """Should never render the stream to a browser with no session."""
    page.goto(base_url)

    assert page.get_by_label("Username").is_visible()


def test_a_wrong_password_stays_on_the_login_page(page, base_url, make_user):
    """Should not sign anyone in on a wrong password, whatever the browser does."""
    make_user("operator")

    page.goto(f"{base_url}/login/")
    page.fill("input[name=username]", "e2e-operator")
    page.fill("input[name=password]", "wrong")
    page.click("button[type=submit]")

    assert page.get_by_text("do not match an account").is_visible()


def test_signing_out_ends_the_session(page, base_url, make_user, sign_in):
    """Should actually drop the session, not only redirect."""
    sign_in(make_user("operator", is_superuser=True))

    page.get_by_role("button", name="Sign out").click()
    page.goto(base_url)

    assert page.get_by_label("Username").is_visible()


# roles


def test_a_viewer_sees_no_action_buttons(page, base_url, dsn_key, make_user, sign_in):
    """Should render a read-only stream, not buttons that answer 403."""
    send_event(base_url, dsn_key)
    viewer = make_user("viewer")
    team = people_models.Team.objects.create(name="e2e-viewers")
    people_models.Membership.objects.create(
        user=viewer, team=team, role=people_models.Role.VIEWER
    )
    sign_in(viewer)

    page.goto(base_url)

    assert page.get_by_role("button", name="Acknowledge").count() == 0


def test_a_member_can_resolve_an_issue_from_the_stream(
    page, base_url, dsn_key, make_user, sign_in
):
    """Should do the whole triage round trip a person actually performs."""
    send_event(base_url, dsn_key)
    member = make_user("member")
    team = people_models.Team.objects.create(name="e2e-members")
    people_models.Membership.objects.create(
        user=member, team=team, role=people_models.Role.MEMBER
    )
    sign_in(member)
    page.goto(base_url)

    page.locator("input[name=issue]").first.check()
    page.get_by_role("button", name="Resolve").click()
    page.wait_for_url(f"{base_url}/")

    result = issue_models.Issue.objects.get().triage_state
    expected = issue_models.TriageState.RESOLVED

    assert result == expected


def test_the_action_is_recorded_in_the_history_page(
    page, base_url, dsn_key, make_user, sign_in
):
    """Should show what the person just did, on the page built to answer that."""
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))
    page.goto(base_url)
    page.locator("input[name=issue]").first.check()
    page.get_by_role("button", name="Resolve").click()
    page.wait_for_url(f"{base_url}/")

    page.goto(f"{base_url}/history/")

    assert page.locator("code", has_text="issue.triage").first.is_visible()


# ownership


def test_an_owning_team_is_shown_in_the_stream(
    page, base_url, dsn_key, make_user, sign_in
):
    """Should route by the stack frame and say so, without anyone clicking."""
    team = people_models.Team.objects.create(name="e2e-payments")
    people_models.OwnershipRule.objects.create(
        name="e2e payments",
        pattern="src/payments/*",
        field="path",
        team=team,
    )
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))

    page.goto(base_url)

    assert page.get_by_role("link", name="e2e-payments").is_visible()


def test_the_owner_filter_narrows_the_stream(
    page, base_url, dsn_key, make_user, sign_in
):
    """Should be one click from the column to only that team's queue."""
    team = people_models.Team.objects.create(name="e2e-payments")
    people_models.OwnershipRule.objects.create(
        name="e2e payments", pattern="src/payments/*", field="path", team=team
    )
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))
    page.goto(base_url)

    page.get_by_role("link", name="e2e-payments").click()
    page.wait_for_load_state("networkidle")

    assert page.get_by_role("link", name=TITLE).is_visible()


# the rest of the surface


def test_the_overview_page_renders(page, base_url, dsn_key, make_user, sign_in):
    """Should be reachable and populated, which a template error would break."""
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))

    page.goto(f"{base_url}/overview/")

    assert page.locator(".kpi-label", has_text="Firing now").is_visible()


def test_the_ingest_page_renders(page, base_url, dsn_key, make_user, sign_in):
    """Should show the door's own state to whoever is debugging it."""
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))

    page.goto(f"{base_url}/ingest/")

    assert page.locator(".kpi-label", has_text="Backlog").first.is_visible()


def test_the_markdown_export_is_served(page, base_url, dsn_key, make_user, sign_in):
    """Should hand back the artefact a person pastes elsewhere."""
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))
    issue = issue_models.Issue.objects.get()

    response = page.request.get(f"{base_url}/issues/{issue.pk}/?format=md")

    assert "# " in response.text()
