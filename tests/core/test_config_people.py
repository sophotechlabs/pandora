import io
import textwrap

import pytest
from django.contrib.auth import models as auth_models
from django.core import management
from django.core.management.base import CommandError

from pandora.people import ownership
from pandora.people.models import Membership, OwnershipRule, Role, Team

pytestmark = pytest.mark.django_db


@pytest.fixture
def write(tmp_path):
    def _write(body):
        path = tmp_path / "pandora.yaml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return str(path)

    return _write


def doc(*parts):
    return "\n".join(textwrap.dedent(part).strip("\n") for part in parts) + "\n"


def run(path, *args):
    out = io.StringIO()
    management.call_command("apply_config", "--path", path, *args, stdout=out)
    return out.getvalue()


PROJECTS = """
    projects:
      - slug: infrastructure
        name: Infrastructure
      - slug: apps
        name: Applications
    """


# teams


def test_a_team_is_created_from_the_file(write):
    """Should let the whole install be described in one file under version control."""
    run(
        write(
            doc(
                PROJECTS,
                """
            teams:
              - name: platform
            """,
            )
        )
    )

    result = list(Team.objects.values_list("name", flat=True))
    expected = ["platform"]

    assert result == expected


def test_a_team_is_scoped_to_the_projects_it_names(write):
    """Should be where the scope is set — not a checkbox someone forgets."""
    run(
        write(
            doc(
                PROJECTS,
                """
            teams:
              - name: platform
                projects: [infrastructure]
            """,
            )
        )
    )

    result = list(Team.objects.get().projects.values_list("slug", flat=True))
    expected = ["infrastructure"]

    assert result == expected


def test_a_removed_project_is_taken_off_the_team(write):
    """Should reconcile, not accumulate — the file is the whole truth."""
    both = doc(
        PROJECTS,
        """
        teams:
          - name: platform
            projects: [infrastructure, apps]
        """,
    )
    one = doc(
        PROJECTS,
        """
        teams:
          - name: platform
            projects: [infrastructure]
        """,
    )
    run(write(both))

    run(write(one))

    result = list(Team.objects.get().projects.values_list("slug", flat=True))
    expected = ["infrastructure"]

    assert result == expected


def test_a_member_named_as_a_string_gets_the_member_role(write):
    """Should keep the common case to one line."""
    run(
        write(
            doc(
                PROJECTS,
                """
            teams:
              - name: platform
                members: [dev]
            """,
            )
        )
    )

    result = Membership.objects.get().role
    expected = Role.MEMBER

    assert result == expected


def test_a_member_can_be_given_a_role(write):
    """Should be able to say who owns the install without opening the admin."""
    run(
        write(
            doc(
                PROJECTS,
                """
            teams:
              - name: platform
                members:
                  - user: boss
                    role: owner
            """,
            )
        )
    )

    result = Membership.objects.get().role
    expected = Role.OWNER

    assert result == expected


def test_a_named_member_who_has_no_account_gets_one(write):
    """Should let an operator write the file before anyone has signed in."""
    run(
        write(
            doc(
                PROJECTS,
                """
            teams:
              - name: platform
                members: [dev]
            """,
            )
        )
    )

    result = auth_models.User.objects.get(username="dev").is_staff

    assert result is True


def test_a_provisioned_member_cannot_sign_in_with_a_password(write, client):
    """Should not create an account with a guessable or empty password."""
    run(
        write(
            doc(
                PROJECTS,
                """
            teams:
              - name: platform
                members: [dev]
            """,
            )
        )
    )

    result = auth_models.User.objects.get(username="dev").has_usable_password()

    assert result is False


def test_a_member_dropped_from_the_file_loses_the_membership(write):
    """Should be how access is revoked — delete the line, apply the file."""
    two = doc(
        PROJECTS,
        """
        teams:
          - name: platform
            members: [dev, ops]
        """,
    )
    one = doc(
        PROJECTS,
        """
        teams:
          - name: platform
            members: [dev]
        """,
    )
    run(write(two))

    run(write(one))

    result = list(Membership.objects.values_list("user__username", flat=True))
    expected = ["dev"]

    assert result == expected


def test_a_changed_role_is_written_through(write):
    """Should let a promotion be a one-word diff."""
    before = doc(
        PROJECTS,
        """
        teams:
          - name: platform
            members:
              - user: dev
                role: viewer
        """,
    )
    after = doc(
        PROJECTS,
        """
        teams:
          - name: platform
            members:
              - user: dev
                role: owner
        """,
    )
    run(write(before))

    run(write(after))

    result = Membership.objects.get().role
    expected = Role.OWNER

    assert result == expected


def test_an_unknown_role_is_refused(write):
    """Should fail on the file rather than silently granting the default."""
    body = write(
        doc(
            PROJECTS,
            """
        teams:
          - name: platform
            members:
              - user: dev
                role: admin
        """,
        )
    )

    with pytest.raises(CommandError, match="unknown role"):
        run(body)


def test_a_member_with_no_user_is_refused(write):
    """Should not create a membership nobody holds."""
    body = write(
        doc(
            PROJECTS,
            """
        teams:
          - name: platform
            members:
              - role: owner
        """,
        )
    )

    with pytest.raises(CommandError, match="no user"):
        run(body)


def test_a_team_naming_an_unknown_project_is_refused(write):
    """Should catch the typo instead of silently scoping the team to nothing."""
    body = write(
        doc(
            PROJECTS,
            """
        teams:
          - name: platform
            projects: [infra]
        """,
        )
    )

    with pytest.raises(CommandError, match="unknown project"):
        run(body)


def test_an_unchanged_team_is_reported_as_unchanged(write):
    """Should make a re-apply readable — noise hides the one line that changed."""
    body = write(
        doc(
            PROJECTS,
            """
        teams:
          - name: platform
            members: [dev]
        """,
        )
    )
    run(body)

    output = run(body)

    assert "0 updated" in output


# ownership rules


def test_an_ownership_rule_is_created_from_the_file(write):
    """Should be reviewable in a pull request, like the code it routes."""
    run(
        write(
            doc(
                PROJECTS,
                """
            teams:
              - name: platform
            ownership_rules:
              - name: payments
                pattern: src/payments/*
                team: platform
            """,
            )
        )
    )

    rule = OwnershipRule.objects.get()
    result = (rule.pattern, rule.field, rule.team.name)
    expected = ("src/payments/*", ownership.PATH, "platform")

    assert result == expected


def test_an_ownership_rule_can_name_a_person(write):
    """Should route to whoever wrote it when there is no team to speak of."""
    run(
        write(
            doc(
                PROJECTS,
                """
            ownership_rules:
              - name: payments
                pattern: src/payments/*
                user: dev
            """,
            )
        )
    )

    result = OwnershipRule.objects.get().user.username
    expected = "dev"

    assert result == expected


def test_an_ownership_rule_can_match_on_another_field(write):
    """Should route by URL or tag when the stack frames say nothing useful."""
    run(
        write(
            doc(
                PROJECTS,
                """
            ownership_rules:
              - name: checkout
                pattern: "https://shop.test/checkout*"
                field: url
                user: dev
            """,
            )
        )
    )

    result = OwnershipRule.objects.get().field
    expected = ownership.URL

    assert result == expected


def test_an_ownership_rule_naming_both_a_team_and_a_person_is_refused(write):
    """Should keep one owner per rule — two is a question, not a routing."""
    body = write(
        doc(
            PROJECTS,
            """
        teams:
          - name: platform
        ownership_rules:
          - name: payments
            pattern: src/payments/*
            team: platform
            user: dev
        """,
        )
    )

    with pytest.raises(CommandError, match="both a team and a user"):
        run(body)


def test_an_ownership_rule_naming_nobody_is_refused(write):
    """Should not store a rule that can never assign anything."""
    body = write(
        doc(
            PROJECTS,
            """
        ownership_rules:
          - name: payments
            pattern: src/payments/*
        """,
        )
    )

    with pytest.raises(CommandError, match="names no owner"):
        run(body)


def test_an_ownership_rule_on_an_unknown_field_is_refused(write):
    """Should catch the typo rather than never matching anything."""
    body = write(
        doc(
            PROJECTS,
            """
        ownership_rules:
          - name: payments
            pattern: src/payments/*
            field: filename
            user: dev
        """,
        )
    )

    with pytest.raises(CommandError, match="unknown field"):
        run(body)


def test_an_ownership_rule_naming_an_unknown_team_is_refused(write):
    """Should not quietly create a team from a misspelling."""
    body = write(
        doc(
            PROJECTS,
            """
        ownership_rules:
          - name: payments
            pattern: src/payments/*
            team: platfrom
        """,
        )
    )

    with pytest.raises(CommandError, match="unknown team"):
        run(body)


def test_a_rule_dropped_from_the_file_is_deactivated(write):
    """Should stop routing without losing the record of what used to route."""
    two = doc(
        PROJECTS,
        """
        ownership_rules:
          - name: payments
            pattern: src/payments/*
            user: dev
          - name: search
            pattern: src/search/*
            user: dev
        """,
    )
    one = doc(
        PROJECTS,
        """
        ownership_rules:
          - name: payments
            pattern: src/payments/*
            user: dev
        """,
    )
    run(write(two))

    run(write(one))

    result = sorted(OwnershipRule.objects.values_list("name", "active"))
    expected = [("payments", True), ("search", False)]

    assert result == expected


def test_a_rule_can_be_scoped_to_one_project(write):
    """Should keep one project's routing out of another's."""
    run(
        write(
            doc(
                PROJECTS,
                """
            ownership_rules:
              - name: payments
                pattern: src/payments/*
                project: apps
                user: dev
            """,
            )
        )
    )

    result = OwnershipRule.objects.get().project.slug
    expected = "apps"

    assert result == expected


def test_a_dry_run_writes_nothing(write):
    """Should be safe to run against production to see what would change."""
    body = write(
        doc(
            PROJECTS,
            """
        teams:
          - name: platform
            members: [dev]
        """,
        )
    )

    output = run(body, "--dry-run")

    result = (Team.objects.count(), "rolled back" in output)
    expected = (0, True)

    assert result == expected
