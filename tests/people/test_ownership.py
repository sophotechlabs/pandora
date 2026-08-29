import pytest
from django.utils import timezone

from pandora.core import models as core_models
from pandora.events.types import Event, new_event_id
from pandora.people import ownership
from pandora.people.models import Assignment, OwnershipRule

pytestmark = pytest.mark.django_db


@pytest.fixture
def rule(make_team):
    def build(**overrides):
        fields = {
            "name": "payments",
            "pattern": "src/payments/*",
            "field": ownership.PATH,
            "team": overrides.pop("team", None) or make_team("payments"),
        }
        fields.update(overrides)
        return OwnershipRule.objects.create(**fields)

    return build


@pytest.fixture
def make_event(project):
    def build(**overrides):
        fields = {
            "id": new_event_id(),
            "project_id": project.pk,
            "timestamp": timezone.now(),
            "level": "error",
            "message": "boom",
        }
        fields.update(overrides)
        return Event(**fields)

    return build


def frames(*paths):
    return {
        "exceptions": [
            {"frames": [{"filename": path} for path in paths]},
        ]
    }


# what a rule can match on


def test_a_path_rule_matches_a_stack_frame(make_issue, make_event, rule):
    """Should route by the file that broke — the only signal most events carry."""
    rule()
    matched = ownership.matching(
        make_issue(), make_event(payload=frames("src/payments/charge.py"))
    )

    result = [row.name for row in matched]
    expected = ["payments"]

    assert result == expected


def test_a_path_rule_ignores_a_frame_from_another_directory(
    make_issue, make_event, rule
):
    """Should not claim an issue that merely passed through the same process."""
    rule()
    matched = ownership.matching(
        make_issue(), make_event(payload=frames("src/search/index.py"))
    )

    assert matched == []


def test_a_url_rule_matches_the_request(make_issue, make_event, rule):
    """Should route a web error by the route that produced it."""
    rule(field=ownership.URL, pattern="https://shop.test/checkout*")
    event = make_event(payload={"request": {"url": "https://shop.test/checkout/pay"}})

    matched = ownership.matching(make_issue(), event)

    assert len(matched) == 1


def test_a_tag_rule_matches_an_event_tag(make_issue, make_event, rule):
    """Should route by whatever the SDK was told to send."""
    rule(field=ownership.TAG, pattern="service=gateway")
    event = make_event(tags={"service": "gateway"})

    matched = ownership.matching(make_issue(), event)

    assert len(matched) == 1


def test_a_tag_rule_matches_a_grouping_label(make_issue, rule):
    """Should route an Alertmanager issue, which has labels and no event."""
    rule(field=ownership.TAG, pattern="namespace=payments")

    matched = ownership.matching(make_issue(), None)

    assert len(matched) == 1


def test_a_culprit_rule_matches_without_any_event(make_issue, rule):
    """Should still route when the payload was dropped or never stored."""
    rule(field=ownership.CULPRIT, pattern="checkout.*")

    matched = ownership.matching(make_issue(), None)

    assert len(matched) == 1


def test_a_module_frame_counts_as_a_path(make_issue, make_event, rule):
    """Should route a language whose frames carry a module, not a filename."""
    rule(pattern="payments.*")
    event = make_event(
        payload={"exceptions": [{"frames": [{"module": "payments.gw"}]}]}
    )

    matched = ownership.matching(make_issue(), event)

    assert len(matched) == 1


def test_matching_is_case_sensitive(make_issue, make_event, rule):
    """Should not match Src/Payments on a case-sensitive filesystem."""
    rule()
    matched = ownership.matching(
        make_issue(), make_event(payload=frames("SRC/PAYMENTS/charge.py"))
    )

    assert matched == []


# which rules are considered


def test_a_rule_for_another_project_does_not_apply(make_issue, make_event, rule):
    """Should keep one project's routing out of another's."""
    other = core_models.Project.objects.create(slug="apps", name="Applications")
    rule(project=other)

    matched = ownership.matching(
        make_issue(), make_event(payload=frames("src/payments/charge.py"))
    )

    assert matched == []


def test_a_rule_for_this_project_applies(make_issue, make_event, rule, project):
    """Should let a rule be narrowed to the project it was written for."""
    rule(project=project)

    matched = ownership.matching(
        make_issue(), make_event(payload=frames("src/payments/charge.py"))
    )

    assert len(matched) == 1


def test_an_inactive_rule_is_skipped(make_issue, make_event, rule):
    """Should let a rule be parked without deleting it."""
    rule(active=False)

    matched = ownership.matching(
        make_issue(), make_event(payload=frames("src/payments/charge.py"))
    )

    assert matched == []


def test_rules_come_back_in_their_configured_order(make_issue, rule):
    """Should decide the order suggestions are shown in."""
    rule(name="second", field=ownership.CULPRIT, pattern="checkout*", ordering=200)
    rule(name="first", field=ownership.CULPRIT, pattern="checkout*", ordering=10)

    result = [row.name for row in ownership.matching(make_issue(), None)]
    expected = ["first", "second"]

    assert result == expected


# assignment


def test_one_matching_rule_assigns_the_issue(make_issue, make_event, rule):
    """Should do the routing without asking anyone, when it is unambiguous."""
    rule()
    issue = make_issue()

    assignment = ownership.assign(
        issue, make_event(payload=frames("src/payments/charge.py"))
    )

    result = (assignment.issue_id, assignment.team.name)
    expected = (issue.pk, "payments")

    assert result == expected


def test_two_matching_rules_assign_nobody(make_issue, rule):
    """Should not guess between two owners — a wrong owner is worse than none."""
    rule(name="a", field=ownership.CULPRIT, pattern="checkout*")
    rule(name="b", field=ownership.CULPRIT, pattern="*gateway*")

    result = ownership.assign(make_issue(), None)

    assert result is None


def test_two_matching_rules_are_offered_as_suggestions(make_issue, rule):
    """Should hand the ambiguity to a person instead of hiding it."""
    rule(name="a", field=ownership.CULPRIT, pattern="checkout*")
    rule(name="b", field=ownership.CULPRIT, pattern="*gateway*")

    result = [row.name for row in ownership.suggestions(make_issue(), None)]
    expected = ["a", "b"]

    assert result == expected


def test_an_unambiguous_match_makes_no_suggestion(make_issue, rule):
    """Should not repeat the rule that already did the assigning."""
    rule(field=ownership.CULPRIT, pattern="checkout*")

    result = ownership.suggestions(make_issue(), None)

    assert result == []


def test_assigning_twice_moves_the_issue_rather_than_duplicating_it(
    make_issue, make_event, rule, make_user
):
    """Should keep one owner per issue when the rules are edited."""
    rule()
    issue = make_issue()
    event = make_event(payload=frames("src/payments/charge.py"))
    ownership.assign(issue, event)
    OwnershipRule.objects.update(user=make_user("dev"), team=None)

    ownership.assign(issue, event)

    result = (Assignment.objects.count(), Assignment.objects.get().user.username)
    expected = (1, "dev")

    assert result == expected


def test_owners_of_maps_issues_to_their_assignment(make_issue, rule):
    """Should let the stream show owners without a query per row."""
    rule(field=ownership.CULPRIT, pattern="checkout*")
    first = make_issue(title="one")
    second = make_issue(title="two")
    ownership.assign(first, None)

    result = list(ownership.owners_of([first, second]))
    expected = [first.pk]

    assert result == expected


def test_a_rule_reads_as_its_field_and_pattern(rule):
    """Should be pickable from a list in the admin."""
    result = str(rule())
    expected = "payments (path:src/payments/*)"

    assert result == expected


def test_an_assignment_reads_as_who_owns_it(make_issue, rule, make_user):
    """Should answer the admin's only question about an assignment row."""
    rule(team=None, user=make_user("dev"), field=ownership.CULPRIT, pattern="checkout*")
    issue = make_issue()

    result = str(ownership.assign(issue, None))
    expected = f"{issue.pk} to dev"

    assert result == expected
