import http

import pytest

from pandora.issues import models as issue_models
from pandora.people.models import AuditEntry

pytestmark = pytest.mark.django_db


def merge_post(session, issues, next_url="/"):
    return session.post(
        "/issues/actions/",
        {"issue": [issue.pk for issue in issues], "action": "merge", "next": next_url},
    )


# merging from the stream


def test_two_issues_merge_into_one(operator_client, make_issue):
    """Should be two clicks from noticing a split to fixing it."""
    first = make_issue(title="One")
    second = make_issue(title="Two")

    merge_post(operator_client, [first, second])

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_the_oldest_issue_is_the_one_that_survives(operator_client, make_issue):
    """Should keep the row whose first_seen is the fault's real beginning."""
    first = make_issue(title="One")
    second = make_issue(title="Two")

    merge_post(operator_client, [first, second])

    result = issue_models.Issue.objects.get().pk
    expected = first.pk

    assert result == expected


def test_merging_one_issue_asks_for_another(operator_client, make_issue):
    """Should say what is missing rather than silently doing nothing."""
    issue = make_issue()

    response = merge_post(operator_client, [issue], next_url="/")

    body = operator_client.get(response.url).content.decode()

    assert "at least two issues" in body


def test_two_projects_cannot_be_merged(operator_client, make_issue, other_project):
    """Should refuse rather than move an issue between projects by accident."""
    first = make_issue()
    second = make_issue(project=other_project)

    response = merge_post(operator_client, [first, second])
    body = operator_client.get(response.url).content.decode()

    assert "Two projects cannot be merged" in body


def test_a_viewer_may_not_merge(client, make_issue, django_user_model):
    """Should sit behind the same permission as every other triage action."""
    from pandora.people.models import Membership, Role, Team

    viewer = django_user_model.objects.create_user(
        username="viewer", password="pass", is_staff=True
    )
    Membership.objects.create(
        user=viewer, team=Team.objects.create(name="watchers"), role=Role.VIEWER
    )
    client.force_login(viewer)

    response = merge_post(client, [make_issue(), make_issue()])

    result = response.status_code
    expected = http.HTTPStatus.FORBIDDEN

    assert result == expected


def test_a_merge_is_recorded_in_the_history(operator_client, make_issue):
    """Should be answerable later: who decided these were the same thing."""
    merge_post(operator_client, [make_issue(), make_issue()])

    entry = AuditEntry.objects.get(action="issue.merge")
    result = (entry.actor, entry.data["folded"])
    expected = ("operator", 1)

    assert result == expected


# the issue page


def test_the_page_lists_what_was_merged_in(operator_client, make_issue):
    """Should show the merge, so it can be undone by whoever finds it wrong."""
    first = make_issue(title="One")
    second = make_issue(title="Two")
    merge_post(operator_client, [first, second])

    body = operator_client.get(f"/issues/{first.pk}/").content.decode()

    assert "Merged in" in body and "Two" in body


def test_the_page_offers_the_rule_that_would_have_avoided_the_merge(
    operator_client, make_issue
):
    """Should turn a labelled example into a rule, which is the whole idea."""
    shared = {"alertname": "TargetDown", "namespace": "payments"}
    first = make_issue(title="One", grouping_labels={**shared, "pod": "one"})
    second = make_issue(title="Two", grouping_labels={**shared, "pod": "two"})
    merge_post(operator_client, [first, second])

    body = operator_client.get(f"/issues/{first.pk}/").content.decode()

    assert "would have made this merge unnecessary" in body


def test_the_page_names_the_label_that_split_the_issue(operator_client, make_issue):
    """Should point at the label to deny, not only at the fact of a split."""
    shared = {"alertname": "TargetDown", "namespace": "payments"}
    first = make_issue(title="One", grouping_labels={**shared, "pod": "one"})
    second = make_issue(title="Two", grouping_labels={**shared, "pod": "two"})
    merge_post(operator_client, [first, second])

    body = operator_client.get(f"/issues/{first.pk}/").content.decode()

    assert "differ on" in body and "pod" in body


def test_an_unmerged_issue_shows_no_merge_card(operator_client, make_issue):
    """Should not put an empty panel on every issue page."""
    issue = make_issue()

    body = operator_client.get(f"/issues/{issue.pk}/").content.decode()

    assert "Merged in" not in body


def test_unmerging_removes_the_alias(operator_client, make_issue):
    """Should let the next occurrence open its own issue again."""
    first = make_issue(title="One")
    second = make_issue(title="Two")
    digest = second.fingerprint_hash
    merge_post(operator_client, [first, second])

    operator_client.post(f"/issues/{first.pk}/unmerge/{digest}/")

    result = issue_models.IssueAlias.objects.count()
    expected = 0

    assert result == expected


def test_unmerging_says_what_it_did_and_did_not_do(operator_client, make_issue):
    """Should be honest that the counted history stays where the merge put it."""
    first = make_issue(title="One")
    second = make_issue(title="Two")
    digest = second.fingerprint_hash
    merge_post(operator_client, [first, second])

    response = operator_client.post(
        f"/issues/{first.pk}/unmerge/{digest}/", follow=True
    )

    assert "stays here" in response.content.decode()


def test_unmerging_a_fingerprint_that_is_not_merged_says_so(
    operator_client, make_issue
):
    """Should answer rather than 404 on a stale button."""
    issue = make_issue()

    response = operator_client.post(
        f"/issues/{issue.pk}/unmerge/{'c' * 64}/", follow=True
    )

    assert "not merged into this issue" in response.content.decode()


def test_a_viewer_may_not_unmerge(client, make_issue, django_user_model):
    """Should be the same permission as merging."""
    from pandora.people.models import Membership, Role, Team

    viewer = django_user_model.objects.create_user(
        username="viewer", password="pass", is_staff=True
    )
    Membership.objects.create(
        user=viewer, team=Team.objects.create(name="watchers"), role=Role.VIEWER
    )
    client.force_login(viewer)
    issue = make_issue()

    response = client.post(f"/issues/{issue.pk}/unmerge/{'c' * 64}/")

    result = response.status_code
    expected = http.HTTPStatus.FORBIDDEN

    assert result == expected


def test_an_unmerge_is_recorded(operator_client, make_issue):
    """Should leave the trail in both directions."""
    first = make_issue(title="One")
    second = make_issue(title="Two")
    digest = second.fingerprint_hash
    merge_post(operator_client, [first, second])

    operator_client.post(f"/issues/{first.pk}/unmerge/{digest}/")

    result = AuditEntry.objects.filter(action="issue.unmerge").count()
    expected = 1

    assert result == expected


def test_an_issue_outside_the_scope_cannot_be_unmerged(
    client, make_issue, other_project, django_user_model
):
    """Should answer 404 like every other out-of-scope issue."""
    from pandora.people.models import Membership, Role, Team

    member = django_user_model.objects.create_user(
        username="member", password="pass", is_staff=True
    )
    team = Team.objects.create(name="platform")
    team.projects.add(make_issue().project)
    Membership.objects.create(user=member, team=team, role=Role.MEMBER)
    client.force_login(member)
    outside = make_issue(project=other_project)

    response = client.post(f"/issues/{outside.pk}/unmerge/{'c' * 64}/")

    result = response.status_code
    expected = http.HTTPStatus.NOT_FOUND

    assert result == expected
