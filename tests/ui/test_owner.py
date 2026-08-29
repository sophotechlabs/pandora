import pytest
from django.utils import timezone

from pandora.events import types
from pandora.people import ownership
from pandora.people.models import Assignment, OwnershipRule, Team
from tests.web import fakes

pytestmark = pytest.mark.django_db


@pytest.fixture
def own(db):
    def build(issue, team=None, user=None):
        return Assignment.objects.create(issue=issue, team=team, user=user)

    return build


@pytest.fixture
def team(db):
    return Team.objects.create(name="payments")


@pytest.fixture
def stored_events(mocker):
    def install(events):
        store = fakes.FakeEventStore(events)
        mocker.patch("pandora.ui.views.get_store", return_value=store)
        return store

    return install


def titles(response):
    return [row.issue.title for row in response.context["rows"]]


# the column


def test_the_stream_names_the_owning_team(operator_client, make_issue, own, team):
    """Should answer "is this mine" without opening the issue."""
    own(make_issue(), team=team)

    body = operator_client.get("/").content.decode()

    assert "payments" in body


def test_the_stream_names_the_owning_person(operator_client, make_issue, own, operator):
    """Should show a person the same way it shows a team."""
    own(make_issue(), user=operator)

    body = operator_client.get("/").content.decode()

    assert ">operator</a>" in body


def test_an_unowned_issue_shows_a_dash(operator_client, make_issue):
    """Should show the column is empty rather than looking broken."""
    make_issue()

    row = operator_client.get("/").context["rows"][0]

    result = row.owner
    expected = ""

    assert result == expected


def test_the_issue_page_names_the_owner(operator_client, make_issue, own, team):
    """Should be on the page a person opens after being paged."""
    issue = make_issue()
    own(issue, team=team)

    body = operator_client.get(f"/issues/{issue.pk}/").content.decode()

    assert "owned by payments" in body


def test_the_issue_page_says_when_nobody_owns_it(operator_client, make_issue):
    """Should be visible that routing did not happen, not merely absent."""
    issue = make_issue()

    body = operator_client.get(f"/issues/{issue.pk}/").content.decode()

    assert "Owned by nobody" in body


def test_an_ambiguous_issue_lists_the_teams_that_claim_it(
    operator_client, make_issue, team
):
    """Should show the ambiguity that stopped the automatic assignment."""
    issue = make_issue()
    search = Team.objects.create(name="search")
    for name, owner in (("a", team), ("b", search)):
        OwnershipRule.objects.create(
            name=name,
            pattern="alertname=*",
            field=ownership.CULPRIT,
            team=owner,
        )

    body = operator_client.get(f"/issues/{issue.pk}/").content.decode()

    assert "payments, search each claim it" in body


# the filter


def test_the_stream_can_be_filtered_to_one_team(operator_client, make_issue, own, team):
    """Should be the first thing anyone types on a shared install."""
    own(make_issue(title="ours"), team=team)
    make_issue(title="theirs")

    result = titles(operator_client.get("/?q=owner:payments"))
    expected = ["ours"]

    assert result == expected


def test_the_stream_can_be_filtered_to_one_person(
    operator_client, make_issue, own, operator
):
    """Should work for a person as well as a team."""
    own(make_issue(title="ours"), user=operator)
    make_issue(title="theirs")

    result = titles(operator_client.get("/?q=owner:operator"))
    expected = ["ours"]

    assert result == expected


def test_owner_me_resolves_to_the_signed_in_person(
    operator_client, make_issue, own, operator
):
    """Should not make someone type their own username to see their queue."""
    own(make_issue(title="ours"), user=operator)
    make_issue(title="theirs")

    result = titles(operator_client.get("/?q=owner:me"))
    expected = ["ours"]

    assert result == expected


def test_owner_none_finds_what_nothing_routed(operator_client, make_issue, own, team):
    """Should surface the gap in the ownership rules, which is where they rot."""
    own(make_issue(title="ours"), team=team)
    make_issue(title="unrouted")

    result = titles(operator_client.get("/?q=owner:none"))
    expected = ["unrouted"]

    assert result == expected


def test_two_owner_terms_widen_the_search(operator_client, make_issue, own, team):
    """Should behave like the other filters — repeats are an or, not an and."""
    search = Team.objects.create(name="search")
    own(make_issue(title="ours"), team=team)
    own(make_issue(title="theirs"), team=search)
    make_issue(title="unrouted")

    result = sorted(titles(operator_client.get("/?q=owner:payments owner:search")))
    expected = ["ours", "theirs"]

    assert result == expected


def test_an_owner_nobody_has_matches_nothing(operator_client, make_issue, own, team):
    """Should return nothing rather than everything on a typo."""
    own(make_issue(title="ours"), team=team)

    result = titles(operator_client.get("/?q=owner:paymnets"))
    expected = []

    assert result == expected


def test_an_assignment_with_no_owner_shows_as_unowned(operator_client, make_issue, own):
    """Should not render an empty owner chip when a rule was deleted mid-flight."""
    own(make_issue())

    row = operator_client.get("/").context["rows"][0]

    result = row.owner
    expected = ""

    assert result == expected


def test_a_stored_event_is_used_for_the_suggestions(
    operator_client, make_issue, stored_events, team
):
    """Should read the stack frames, which is where most rules match."""
    issue = make_issue()
    stored_events(
        [
            types.Event(
                id="01J8ZQ7X4N9",
                project_id=issue.project_id,
                issue_id=issue.pk,
                timestamp=timezone.now(),
                level="error",
                message="boom",
                payload={
                    "exceptions": [{"frames": [{"filename": "src/payments/charge.py"}]}]
                },
            )
        ]
    )
    for name in ("a", "b"):
        OwnershipRule.objects.create(
            name=name,
            pattern="src/payments/*",
            field=ownership.PATH,
            team=Team.objects.create(name=f"team-{name}"),
        )

    body = operator_client.get(f"/issues/{issue.pk}/").content.decode()

    assert "team-a, team-b each claim it" in body
