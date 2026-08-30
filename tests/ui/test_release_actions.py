import pytest

from pandora.issues import models as issue_models
from pandora.releases import models as release_models

pytestmark = pytest.mark.django_db


@pytest.fixture
def release(project):
    def build(version, sort_key=None):
        from pandora.releases.versions import sort_key as compute

        return release_models.Release.objects.create(
            project=project,
            version=version,
            sort_key=sort_key or compute(version),
            parsed=True,
        )

    return build


def resolve_in(session, issue, target):
    return session.post(
        "/issues/actions/",
        {"issue": [issue.pk], "action": f"resolve:{target}", "next": "/"},
    )


# the menu


def test_the_stream_offers_the_release_options(operator_client, make_issue, release):
    """Should be where a person already resolves things."""
    make_issue()
    release("1.2.3")

    body = operator_client.get("/").content.decode()

    assert "in the next release" in body and "in 1.2.3" in body


def test_an_install_with_no_releases_says_so(operator_client, make_issue):
    """Should not offer an empty menu with no explanation."""
    make_issue()

    body = operator_client.get("/").content.decode()

    assert "No release has been seen yet" not in body or "in the next release" in body


# resolving


def test_resolving_in_the_next_release_records_the_boundary(
    operator_client, make_issue, release
):
    """Should write down the promise so a later event can be measured against it."""
    issue = make_issue()
    release("1.2.3")

    resolve_in(operator_client, issue, "next")

    row = release_models.Resolution.objects.get()
    result = (row.in_next, row.release.version)
    expected = (True, "1.2.3")

    assert result == expected


def test_resolving_in_the_current_release_is_not_a_promise(
    operator_client, make_issue, release
):
    """Should hold only against something strictly newer."""
    issue = make_issue()
    release("1.2.3")

    resolve_in(operator_client, issue, "current")

    result = release_models.Resolution.objects.get().in_next

    assert result is False


def test_resolving_in_a_named_release_uses_that_one(
    operator_client, make_issue, release
):
    """Should let a person say which build actually carried the fix."""
    issue = make_issue()
    release("1.2.2")
    release("1.2.3")

    resolve_in(operator_client, issue, "1.2.2")

    result = release_models.Resolution.objects.get().release.version
    expected = "1.2.2"

    assert result == expected


def test_resolving_also_moves_the_triage_state(operator_client, make_issue, release):
    """Should be a resolve, not only a note about one."""
    issue = make_issue()
    release("1.2.3")

    resolve_in(operator_client, issue, "next")

    result = issue_models.Issue.objects.get(pk=issue.pk).triage_state
    expected = issue_models.TriageState.RESOLVED

    assert result == expected


def test_naming_a_release_nobody_has_seen_is_refused(
    operator_client, make_issue, release
):
    """Should say so rather than resolve against nothing."""
    issue = make_issue()

    response = resolve_in(operator_client, issue, "9.9.9")
    body = operator_client.get(response.url).content.decode()

    assert "No release called 9.9.9" in body


def test_the_resolve_is_recorded_in_the_history(operator_client, make_issue, release):
    """Should say which release the promise was made against."""
    from pandora.people.models import AuditEntry

    issue = make_issue()
    release("1.2.3")

    resolve_in(operator_client, issue, "next")

    result = AuditEntry.objects.filter(action="issue.triage").first().data["release"]
    expected = "next"

    assert result == expected


# the suspect deploy panel


def test_the_issue_page_names_the_suspect_deploy(operator_client, make_issue, release):
    """Should answer 'what changed just before this started'."""
    import datetime

    issue = make_issue()
    release_models.Deploy.objects.create(
        release=release("1.2.3"),
        environment="p-mk1",
        started_at=issue.first_seen - datetime.timedelta(minutes=5),
    )

    body = operator_client.get(f"/issues/{issue.pk}/").content.decode()

    assert "Suspect deploy" in body and "1.2.3" in body


def test_an_issue_with_no_deploy_before_it_shows_no_panel(operator_client, make_issue):
    """Should not put an empty card on every issue page."""
    issue = make_issue()

    body = operator_client.get(f"/issues/{issue.pk}/").content.decode()

    assert "Suspect deploy" not in body
