import pytest
from django.contrib.auth import models as auth_models

from pandora.core import models as core_models
from pandora.people import access
from pandora.people.models import Role

pytestmark = pytest.mark.django_db

TRIAGE = "issues.change_issue"
REPLAY = "ingest.change_rawenvelope"


# roles resolve to permissions


def test_a_viewer_may_read_and_nothing_else(make_user, make_team, join):
    """Should let someone watch the queue without letting them change it."""
    user = make_user("watcher")
    join(user, make_team(), Role.VIEWER)

    result = (access.may(user, TRIAGE), access.may(user, REPLAY))
    expected = (False, False)

    assert result == expected


def test_a_member_may_triage(make_user, make_team, join):
    """Should let the people doing the work do the work."""
    user = make_user("dev")
    join(user, make_team(), Role.MEMBER)

    result = (access.may(user, TRIAGE), access.may(user, REPLAY))
    expected = (True, False)

    assert result == expected


def test_an_owner_may_replay(make_user, make_team, join):
    """Should keep replaying the ingest queue to the people who own the install."""
    user = make_user("boss")
    join(user, make_team(), Role.OWNER)

    result = (access.may(user, TRIAGE), access.may(user, REPLAY))
    expected = (True, True)

    assert result == expected


def test_the_highest_role_across_teams_wins(make_user, make_team, join):
    """Should not let membership of a second team quietly demote someone."""
    user = make_user("dev")
    join(user, make_team("platform"), Role.VIEWER)
    join(user, make_team("payments"), Role.OWNER)

    result = access.role_of(user)
    expected = Role.OWNER

    assert result == expected


def test_someone_in_no_team_has_no_role(make_user):
    """Should not grant anything by default — a staff account is not a member."""
    result = access.role_of(make_user("stranger"))

    assert result is None


def test_a_django_permission_still_grants(make_user):
    """Should keep the pre-team access model working for an install that never made a team."""
    user = make_user("legacy")
    user.user_permissions.add(
        auth_models.Permission.objects.get(
            content_type__app_label="issues", codename="change_issue"
        )
    )
    user = auth_models.User.objects.get(pk=user.pk)

    result = access.may(user, TRIAGE)

    assert result is True


def test_a_superuser_may_everything(make_user):
    """Should never lock the operator out of their own install."""
    user = make_user("root", is_superuser=True)

    result = (access.may(user, TRIAGE), access.may(user, REPLAY))
    expected = (True, True)

    assert result == expected


def test_an_anonymous_visitor_has_no_role():
    """Should not read a role off a request nobody signed in on."""
    result = access.role_of(auth_models.AnonymousUser())

    assert result is None


# project scoping


def test_a_member_of_no_team_sees_everything(make_user, project):
    """Should keep a single-operator install working with no configuration at all."""
    result = access.projects_for(make_user("solo"))

    assert result is None


def test_a_team_with_no_projects_scopes_nothing(make_user, make_team, join, project):
    """Should treat an unscoped team as install-wide rather than as access to nothing."""
    user = make_user("dev")
    join(user, make_team(), Role.MEMBER)

    result = access.projects_for(user)

    assert result is None


def test_a_scoped_team_limits_the_projects(make_user, make_team, join, project):
    """Should show a team the projects it owns and no others."""
    core_models.Project.objects.create(slug="apps", name="Applications")
    user = make_user("dev")
    join(user, make_team("platform", projects=[project]), Role.MEMBER)

    result = access.projects_for(user)
    expected = [project.pk]

    assert result == expected


def test_a_superuser_is_never_scoped(make_user, make_team, join, project):
    """Should let the owner see everything even while belonging to one team."""
    user = make_user("root", is_superuser=True)
    join(user, make_team("platform", projects=[project]), Role.MEMBER)

    result = access.projects_for(user)

    assert result is None


def test_two_teams_union_their_projects(make_user, make_team, join, project):
    """Should add access rather than replacing it when someone joins a second team."""
    other = core_models.Project.objects.create(slug="apps", name="Applications")
    user = make_user("dev")
    join(user, make_team("platform", projects=[project]), Role.MEMBER)
    join(user, make_team("apps", projects=[other]), Role.MEMBER)

    result = sorted(access.projects_for(user))
    expected = sorted([project.pk, other.pk])

    assert result == expected


# the permission backend


def test_the_backend_never_authenticates(make_user):
    """Should add permissions to an account, never a second way to sign in."""
    from pandora.people.backends import TeamRoleBackend

    result = TeamRoleBackend().authenticate(None, username="dev", password="x")

    assert result is None


def test_the_backend_grants_nothing_on_a_single_object(make_user, make_team, join):
    """Should not claim per-object authority it does not implement."""
    from pandora.people.backends import TeamRoleBackend

    user = make_user("boss")
    join(user, make_team(), Role.OWNER)

    result = TeamRoleBackend().has_perm(user, TRIAGE, obj=make_team("other"))

    assert result is False


def test_the_backend_lists_the_permissions_of_the_role(make_user, make_team, join):
    """Should show up wherever Django asks for the whole set, templates included."""
    from pandora.people.backends import TeamRoleBackend

    user = make_user("boss")
    join(user, make_team(), Role.OWNER)

    result = TeamRoleBackend().get_all_permissions(user)
    expected = {TRIAGE, REPLAY}

    assert result == expected


def test_a_role_shows_up_in_the_template_permission_lookup(
    client, make_user, make_team, join
):
    """Should work in a template, which is where a missing backend shows up late."""
    user = make_user("dev")
    join(user, make_team(), Role.MEMBER)
    user = auth_models.User.objects.get(pk=user.pk)

    result = user.has_perm(TRIAGE)

    assert result is True
