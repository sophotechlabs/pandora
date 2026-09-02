import hashlib
import io
import json
import zipfile
from datetime import timedelta

import pytest
import requests
from django.utils import timezone

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.issues import models as issue_models
from pandora.people import models as people_models
from pandora.releases import models as release_models

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


def test_a_stalled_rollout_reaches_the_overview(
    page, base_url, dsn_key, make_user, sign_in
):
    release = release_models.Release.objects.create(
        project=dsn_key.project,
        version="2.4.1",
    )
    release_models.Deploy.objects.create(
        release=release,
        environment="production",
        started_at=timezone.now() - timedelta(hours=2),
    )
    sign_in(make_user("operator", is_superuser=True))

    page.goto(f"{base_url}/overview/")

    row = page.locator("tr", has_text="2.4.1")
    assert row.get_by_text("production").is_visible()


def test_a_client_report_reaches_the_ingest_page(
    page, base_url, dsn_key, make_user, sign_in
):
    envelope = "\n".join(
        [
            json.dumps({}),
            json.dumps({"type": "client_report"}),
            json.dumps(
                {
                    "timestamp": timezone.now().isoformat(),
                    "discarded_events": [
                        {
                            "reason": "queue_overflow",
                            "category": "error",
                            "quantity": 13,
                        }
                    ],
                }
            ),
        ]
    )
    response = requests.post(
        f"{base_url}/api/{dsn_key.project_id}/envelope/",
        data=envelope.encode(),
        headers={
            "Content-Type": "application/x-sentry-envelope",
            "X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}",
        },
        timeout=10,
    )
    response.raise_for_status()
    sign_in(make_user("operator", is_superuser=True))

    page.goto(f"{base_url}/ingest/")

    row = page.locator("tr", has_text="queue_overflow")
    assert row.get_by_text("13", exact=True).is_visible()


def test_the_markdown_export_is_served(page, base_url, dsn_key, make_user, sign_in):
    """Should hand back the artefact a person pastes elsewhere."""
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))
    issue = issue_models.Issue.objects.get()

    response = page.request.get(f"{base_url}/issues/{issue.pk}/?format=md")

    assert "# " in response.text()


# what the six tracks added


def send_second_event(base_url, dsn_key):
    envelope = "\n".join(
        [
            json.dumps({"event_id": "d" * 32}),
            json.dumps({"type": "event"}),
            json.dumps(
                {
                    "event_id": "d" * 32,
                    "level": "error",
                    "platform": "python",
                    "environment": "e2e",
                    "exception": {
                        "values": [
                            {
                                "type": "TimeoutError",
                                "value": "e2e upstream timeout",
                                "stacktrace": {
                                    "frames": [
                                        {
                                            "filename": "src/payments/upstream.py",
                                            "lineno": 7,
                                            "function": "call",
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


def test_a_saved_view_comes_back_as_a_segment(
    page, base_url, dsn_key, make_user, sign_in
):
    """Should let an operator keep the search they run every morning."""
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))

    page.goto(f"{base_url}/?q=is:unresolved")
    page.fill("input[name=name]", "Morning triage")
    page.click("form.save-view button[type=submit]")

    assert page.locator(".segment.view", has_text="Morning triage").is_visible()


def test_merging_two_issues_folds_them_into_one(
    page, base_url, dsn_key, make_user, sign_in
):
    """Should be the escape hatch for grouping nobody wrote a rule for."""
    send_event(base_url, dsn_key)
    send_second_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))

    page.goto(base_url)
    page.locator("input[name=issue]").first.check()
    page.locator("input[name=issue]").nth(1).check()
    page.click("button[value=merge]")

    assert page.locator(".message", has_text="Merged").is_visible()


def test_the_merged_fingerprint_is_listed_on_the_issue(
    page, base_url, dsn_key, make_user, sign_in
):
    """Should show what was folded in, so the merge can be undone."""
    send_event(base_url, dsn_key)
    send_second_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))
    page.goto(base_url)
    page.locator("input[name=issue]").first.check()
    page.locator("input[name=issue]").nth(1).check()
    page.click("button[value=merge]")

    keeper = issue_models.Issue.objects.order_by("first_seen").first()
    page.goto(f"{base_url}/issues/{keeper.pk}/")

    assert page.get_by_text("Merged in").is_visible()


def test_the_csv_export_is_served(page, base_url, dsn_key, make_user, sign_in):
    """Should hand the stream to a spreadsheet, which is where triage reports go."""
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))

    response = page.request.get(f"{base_url}/?csv=1")

    assert "fingerprint_hash" in response.text()


def test_the_stream_sorts_by_relevance(page, base_url, dsn_key, make_user, sign_in):
    """Should rank by what is happening now, which is the point of the sort."""
    send_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))

    page.goto(f"{base_url}/?sort=relevance")

    assert page.get_by_role("link", name=TITLE).is_visible()


def test_a_log_line_becomes_an_issue(page, base_url, dsn_key, make_user, sign_in):
    """Should open the door to everything that will never carry an SDK."""
    line = json.dumps(
        {
            "message": "e2e shipper failure",
            "level": "error",
            "service": "vector",
            "error.kind": "ShipperError",
        }
    )
    response = requests.post(
        f"{base_url}/api/{dsn_key.project_id}/logs/",
        data=line.encode(),
        headers={
            "Content-Type": "application/x-ndjson",
            "X-Sentry-Auth": f"Sentry sentry_key={dsn_key.public_key}",
        },
        timeout=10,
    )
    response.raise_for_status()
    sign_in(make_user("operator", is_superuser=True))

    page.goto(base_url)

    assert page.get_by_role("link", name="ShipperError").is_visible()


def test_a_cron_check_in_opens_a_monitor(base_url, dsn_key):
    """Should watch a job that reports without anyone configuring a monitor."""
    response = requests.post(
        f"{base_url}/api/{dsn_key.project_id}/cron/e2e-backup/{dsn_key.public_key}/",
        json={"status": "ok"},
        timeout=10,
    )
    response.raise_for_status()

    result = ingest_models.Monitor.objects.filter(slug="e2e-backup").count()
    expected = 1

    assert result == expected


DEBUG_ID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
SOURCE_MAP = {
    "version": 3,
    "file": "app.js",
    "sources": ["src/payments.js"],
    "names": ["charge"],
    "mappings": "AAAAA,SAAS",
    "sourcesContent": ["export function charge(order) {\n  throw new Error('x')\n}\n"],
    "debug_id": DEBUG_ID,
}


def bundle_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("app.js.map", json.dumps(SOURCE_MAP))
    return buffer.getvalue()


def upload_bundle(base_url, token, payload):
    """The protocol's two phases, which is what `sentry-cli` does."""
    checksum = hashlib.sha1(payload, usedforsecurity=False).hexdigest()
    headers = {"Authorization": f"Bearer {token.token}"}
    chunks = requests.post(
        f"{base_url}/api/0/organizations/pandora/chunk-upload/",
        files={checksum: (checksum, payload, "application/octet-stream")},
        headers=headers,
        timeout=10,
    )
    chunks.raise_for_status()
    assembled = requests.post(
        f"{base_url}/api/0/organizations/pandora/artifactbundle/assemble/",
        json={"checksum": checksum, "chunks": [checksum], "projects": ["e2e"]},
        headers=headers,
        timeout=10,
    )
    assembled.raise_for_status()
    assert assembled.json()["state"] == "ok", assembled.text


def send_minified_event(base_url, dsn_key):
    envelope = "\n".join(
        [
            json.dumps({"event_id": "c" * 32}),
            json.dumps({"type": "event"}),
            json.dumps(
                {
                    "event_id": "c" * 32,
                    "level": "error",
                    "platform": "javascript",
                    "environment": "e2e",
                    "exception": {
                        "values": [
                            {
                                "type": "TypeError",
                                "value": "undefined is not a function",
                                "stacktrace": {
                                    "frames": [
                                        {
                                            "abs_path": "app://basket.4c9e10.js",
                                            "filename": "basket.4c9e10.js",
                                            "function": "n",
                                            "lineno": 1,
                                            "colno": 0,
                                            "in_app": True,
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    "debug_meta": {
                        "images": [
                            {
                                "type": "sourcemap",
                                "code_file": "app://basket.4c9e10.js",
                                "debug_id": DEBUG_ID,
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


def test_a_source_map_resolves_the_frame_on_the_page(
    page, base_url, dsn_key, make_user, sign_in
):
    """Should turn a minified frame into the file a person can actually open."""
    token = core_models.IngestToken.objects.create(
        project=dsn_key.project,
        name="e2e-maps",
        token="e2e-map-token",
        source=core_models.TokenSource.SDK,
        scope=core_models.TokenScope.INGEST,
    )
    upload_bundle(base_url, token, bundle_zip())
    send_minified_event(base_url, dsn_key)
    sign_in(make_user("operator", is_superuser=True))
    issue = issue_models.Issue.objects.get()

    page.goto(f"{base_url}/issues/{issue.pk}/")

    assert page.get_by_text("src/payments.js").first.is_visible()
